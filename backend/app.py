"""Modal app. Everything runs on Modal, including the database.

The database is a SQLite file owned by one container, `Store` (max_containers=1).
That container serves the HTTP API, runs rescoring, and executes every storage
call made by the pollers. It works on a copy on local disk and writes a snapshot
to a Modal Volume every SNAPSHOT_INTERVAL_S seconds and on shutdown; on start it
restores the latest snapshot. At most that interval of writes is lost if the
container is killed, and those writes are re-created by the next polls.

  modal run -m backend.app::poll_source --source-id tfl_road
  modal run -m backend.app::backfill_police      latest month of Met crime data
  modal serve -m backend.app                     API with live reload
  modal deploy -m backend.app                    API + dispatcher and rescore crons
"""

from __future__ import annotations

import os
from typing import Any

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("backend/requirements.txt")
    .add_local_python_source("backend")
    .add_local_dir("backend/sql", "/root/backend/sql")
)

APP_NAME = "london-risk"
app = modal.App(APP_NAME, image=image)
volume = modal.Volume.from_name("london-risk-db", create_if_missing=True)
# Optional Modal secret `london-risk` (TFL_APP_KEY, LLM_MODEL and the provider key).
# Deploy with LONDON_RISK_SECRET=1 once it exists; without it no secret is attached.
secrets = [modal.Secret.from_name("london-risk")] if os.environ.get("LONDON_RISK_SECRET") else []

VOLUME_DIR = "/data"
SNAPSHOT_PATH = f"{VOLUME_DIR}/risk.sqlite"
LOCAL_PATH = "/tmp/risk.sqlite"
SNAPSHOT_INTERVAL_S = 30


@app.cls(volumes={VOLUME_DIR: volume}, max_containers=1, min_containers=1, timeout=3600, secrets=secrets)
@modal.concurrent(max_inputs=100)
class Store:
    @modal.enter()
    def open(self) -> None:
        import shutil
        import threading
        from pathlib import Path

        from . import db

        if Path(SNAPSHOT_PATH).exists():
            shutil.copy(SNAPSHOT_PATH, LOCAL_PATH)
        db.connect(LOCAL_PATH)
        self._stop = threading.Event()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()

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
        from .api import web

        return web


def deployed_store():
    """The Store of the deployed app. Looked up by name because `modal run`
    starts a temporary app whose own Store would hold a separate database."""
    return modal.Cls.from_name(APP_NAME, "Store")()


class RemoteRepo:
    """Repo whose methods execute in the deployed Store container."""

    def __getattr__(self, method: str):
        return lambda *args, **kwargs: deployed_store().call.remote(method, *args, **kwargs)


@app.function(timeout=900, max_containers=20, secrets=secrets)
def poll_source(source_id: str) -> dict[str, int]:
    from . import geocode
    from .pipeline import SOURCES, run_poll

    repo = RemoteRepo()
    geocode.use_cache(repo)
    counts = run_poll(SOURCES[source_id], repo)
    print(source_id, counts)
    return counts


@app.function(schedule=modal.Cron("* * * * *"), secrets=secrets)
def dispatcher() -> None:
    from .models import utcnow
    from .pipeline import is_pollable

    for source_id in RemoteRepo().due_sources(utcnow()):
        if is_pollable(source_id):
            poll_source.spawn(source_id)


@app.function(schedule=modal.Cron("* * * * *"), timeout=120)
def rescore() -> None:
    print("cells written:", deployed_store().rescore.remote())


@app.function(timeout=900)
def backfill_police(month: str = "") -> dict[str, int]:
    """Load one month of police.uk data (default: latest) as the baseline layer."""
    from .scoring import FINE_RES
    from .sources import police_uk

    resolved, crimes = police_uk.fetch_month(month or None)
    points = police_uk.aggregate_points(crimes)
    repo = RemoteRepo()
    repo.replace_baseline(police_uk.baseline_cells(points, months=1, res=FINE_RES), FINE_RES)
    repo.save_crime_points(resolved, police_uk.points_payload(points, resolved))
    counts = {"crimes": len(crimes), "points": len(points)}
    print(resolved, counts)
    return counts
