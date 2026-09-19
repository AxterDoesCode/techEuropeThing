"""Modal app. Everything runs on Modal, including the database.

The database is a SQLite file owned by one container, `Store` (max_containers=1).
That container serves the HTTP API, runs rescoring, and executes every storage
call made by the pollers. It works on a copy on local disk and writes a snapshot
to a Modal Volume every SNAPSHOT_INTERVAL_S seconds and on shutdown; on start it
restores the latest snapshot. At most that interval of writes is lost if the
container is killed, and those writes are re-created by the next polls.

  modal run -m backend.app::poll_source --source-id tfl_road

News extraction: `poll_source` fetches a feed, then starts one `extract_item`
container per new pre-filter candidate (the LLM extraction agent, model from
LLM_MODEL in the `london-risk` secret). `geocode_place` is a single container
shared by all of them.
  modal run -m backend.app::backfill_police      latest month of Met crime data
  modal serve -m backend.app                     API with live reload
  modal deploy -m backend.app                    API + dispatcher and rescore crons
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("backend/requirements.txt")
    .add_local_python_source("backend")
    .add_local_dir("backend/sql", "/root/backend/sql")
)
# The built web client (`npm run build` in web/) is served by the Store container
# at "/", next to the API. Without a build the API is deployed alone.
WEB_DIST_LOCAL = Path(__file__).parent.parent / "web" / "dist"
WEB_DIST_REMOTE = "/root/web_dist"
if WEB_DIST_LOCAL.is_dir():
    image = image.add_local_dir(str(WEB_DIST_LOCAL), WEB_DIST_REMOTE)

APP_NAME = "london-risk"
app = modal.App(APP_NAME, image=image)
volume = modal.Volume.from_name("london-risk-db", create_if_missing=True)
# Walking graph for /api/route, written by build_graph and read by the Store
graph_volume = modal.Volume.from_name("london-risk-graph", create_if_missing=True)
# Modal secret `london-risk`: LLM_MODEL, GOOGLE_API_KEY, optionally TFL_APP_KEY. It
# must be attached unconditionally: the module is imported again inside each
# container, and a condition that differs there changes the function's
# dependencies, which makes every container fail at start.
secrets = [modal.Secret.from_name("london-risk")]

VOLUME_DIR = "/data"
SNAPSHOT_PATH = f"{VOLUME_DIR}/risk.sqlite"
LOCAL_PATH = "/tmp/risk.sqlite"
GRAPH_DIR = "/graph"
GRAPH_PATH = f"{GRAPH_DIR}/walk.npz"
SNAPSHOT_INTERVAL_S = 30


@app.cls(
    volumes={VOLUME_DIR: volume, GRAPH_DIR: graph_volume},
    max_containers=1,
    min_containers=1,
    timeout=3600,
    cpu=1.0,
    memory=2048,
    secrets=secrets,
)
@modal.concurrent(max_inputs=100)
class Store:
    @modal.enter()
    def open(self) -> None:
        import shutil
        import threading
        from pathlib import Path

        from . import db

        os.environ["GRAPH_PATH"] = GRAPH_PATH
        if Path(SNAPSHOT_PATH).exists():
            shutil.copy(SNAPSHOT_PATH, LOCAL_PATH)
        db.connect(LOCAL_PATH)
        self._stop = threading.Event()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()
        # Routing graph and street baseline are prepared in the background so the
        # first /api/route request does not wait for them
        from .api_route import warm_up

        threading.Thread(target=warm_up, daemon=True).start()

    def _snapshot(self) -> None:
        from . import db

        db.snapshot(SNAPSHOT_PATH)
        volume.commit()

    def _snapshot_loop(self) -> None:
        while not self._stop.wait(SNAPSHOT_INTERVAL_S):
            try:
                self._snapshot()
            except Exception as exc:  # keep the loop alive; the next pass retries
                print("snapshot failed:", exc)

    @modal.exit()
    def close(self) -> None:
        self._stop.set()
        self._snapshot()

    @modal.method()
    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Run one SqliteRepo method. Pollers in other containers use this
        through RemoteRepo."""
        from .db import SqliteRepo

        if method.startswith("_"):
            raise ValueError(method)
        return getattr(SqliteRepo(), method)(*args, **kwargs)

    @modal.method()
    def rescore(self) -> int:
        from .db import SqliteRepo
        from .pipeline import run_rescore

        return run_rescore(SqliteRepo())

    @modal.asgi_app()
    def api(self):
        from fastapi.staticfiles import StaticFiles

        from .api import web

        # Mounted after the API routes, so "/api/..." is matched first
        if Path(WEB_DIST_REMOTE).is_dir():
            web.mount("/", StaticFiles(directory=WEB_DIST_REMOTE, html=True), name="web")
        return web


def deployed_store():
    """The Store of the deployed app. Looked up by name because `modal run`
    starts a temporary app whose own Store would hold a separate database."""
    return modal.Cls.from_name(APP_NAME, "Store")()


class RemoteRepo:
    """Repo whose methods execute in the deployed Store container."""

    def __getattr__(self, method: str):
        return lambda *args, **kwargs: deployed_store().call.remote(method, *args, **kwargs)


# Extraction agents that run at the same time. Each article can make up to 8 LLM
# requests, so this bounds the request rate against the provider's quota.
EXTRACT_CONCURRENCY = int(os.environ.get("EXTRACT_CONCURRENCY", "4"))


@app.function(max_containers=1, timeout=120)
def geocode_place(place_text: str):
    """All geocoding runs in this single container: the Nominatim limit of one
    request per second is enforced per process, and results are cached in the database."""
    from . import geocode

    geocode.use_cache(RemoteRepo())
    return geocode.geocode(place_text)


@app.function(timeout=120, max_containers=EXTRACT_CONCURRENCY, secrets=secrets)
def extract_item(source_id: str, raw):
    """One extraction agent run: one news item -> (events, LLM requests)."""
    from . import geocode
    from .pipeline import SOURCES

    geocode.use_remote(geocode_place.remote)
    return SOURCES[source_id].to_events(raw, RemoteRepo())


# The time limit is below the shortest poll interval of an LLM source (300 s), so a
# poll has ended, one way or the other, before the dispatcher starts the next one.
@app.function(timeout=280, max_containers=20, secrets=secrets)
def poll_source(source_id: str) -> dict[str, int]:
    from . import geocode
    from .pipeline import SOURCES, run_poll

    repo = RemoteRepo()
    geocode.use_cache(repo)

    def map_items(items):
        if not items:  # Function.map over no inputs never returns
            return []
        # One container per item, EXTRACT_CONCURRENCY at a time. A failed item is
        # reported as None: it stays pending and the next poll extracts it again.
        results = extract_item.map([source_id] * len(items), items, return_exceptions=True)
        return [None if isinstance(r, Exception) else r for r in results]

    counts = run_poll(SOURCES[source_id], repo, map_items=map_items)
    print(source_id, counts)
    return counts


@app.function(timeout=120, max_containers=1)
def poll_alerts() -> dict[str, Any]:
    """Met Office warnings and UK Emergency Alerts -> `alerts` table. Started by the
    dispatcher every alerts.POLL_INTERVAL_S seconds."""
    from . import alerts

    result = alerts.poll(RemoteRepo())
    print("alerts", result)
    return result


@app.function(schedule=modal.Cron("* * * * *"), secrets=secrets)
def dispatcher() -> None:
    from .models import utcnow
    from .pipeline import is_pollable

    for source_id in RemoteRepo().due_sources(utcnow()):
        if is_pollable(source_id):
            poll_source.spawn(source_id)
    # Official alert feeds (backend/alerts.py); they are not rows of `sources`
    from . import alerts

    if RemoteRepo().claim_alerts_poll(utcnow(), alerts.POLL_INTERVAL_S, list(alerts.SOURCES)):
        poll_alerts.spawn()


@app.function(schedule=modal.Cron("* * * * *"), timeout=120)
def rescore() -> None:
    print("cells written:", deployed_store().rescore.remote())


@app.function(volumes={GRAPH_DIR: graph_volume}, timeout=900, memory=4096)
def backfill_police(month: str = "") -> dict[str, int]:
    """Load the crime baseline: 12 months of MPS LSOA counts per km of walkable
    street, placed on the police.uk street points of one month (default: latest).
    Reads the routing graph for street lengths; run build_graph first, otherwise
    every LSOA is normalised by area (mps_lsoa module docstring)."""
    from .scoring import FINE_RES
    from .sources import mps_lsoa

    result = mps_lsoa.build(mps_lsoa.load_graph_if_present(GRAPH_PATH), month or None)
    repo = RemoteRepo()
    repo.replace_baseline(result.cells(FINE_RES), FINE_RES)
    repo.save_crime_points(result.month, result.payload())
    counts = {k: v for k, v in result.stats.items() if isinstance(v, int)}
    print(result.period, result.month, result.stats)
    return counts


@app.function(volumes={GRAPH_DIR: graph_volume}, timeout=3600, memory=8192)
def build_graph(bbox: str = "") -> dict[str, Any]:
    """Build the walking graph from OpenStreetMap into the graph Volume. Redeploy
    afterwards so the Store container loads the new file.

    bbox is "west,south,east,north"; empty = inner London."""
    from pathlib import Path

    from .tools import build_graph as tool

    box = tuple(float(v) for v in bbox.split(",")) if bbox else tool.INNER_LONDON
    stats = tool.build(box, Path(GRAPH_PATH), 1800, None)
    graph_volume.commit()
    print(stats)
    return stats
