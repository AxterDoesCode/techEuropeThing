"""Run the whole backend on this machine, without Modal.

  python -m backend.local              poll loop + rescore + API on :8000
  python -m backend.local --once       one pass over all sources, rescore, exit
  python -m backend.local --police     also load the latest month of Met crime data

The database is the SQLite file at data/risk.sqlite (override with RISK_DB).
"""

from __future__ import annotations

import argparse
import os
import threading
import time

from . import db, geocode
from .db import SqliteRepo
from .models import utcnow
from .pipeline import SOURCES, is_pollable, run_poll, run_rescore
from .scoring import FINE_RES

TICK_S = 30


def load_police(repo: SqliteRepo) -> None:
    from .sources import mps_lsoa

    # Street length per LSOA comes from the routing graph; without the file every
    # LSOA is normalised by area (mps_lsoa module docstring)
    graph = mps_lsoa.load_graph_if_present(os.environ.get("GRAPH_PATH", "data/graph/walk.npz"))
    result = mps_lsoa.build(graph)
    repo.replace_baseline(result.cells(FINE_RES), FINE_RES)
    repo.save_crime_points(result.month, result.payload())
    print(f"MPS LSOA {result.period} on police.uk {result.month} street points: {result.stats}")


def tick(repo: SqliteRepo) -> None:
    for source_id in repo.due_sources(utcnow()):
        if not is_pollable(source_id):
            continue
        try:
            print(source_id, run_poll(SOURCES[source_id], repo))
        except RuntimeError as exc:  # already recorded in agent_runs
            print(exc)
    print("cells written:", run_rescore(repo))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--police", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    db.connect(os.environ.get("RISK_DB", "data/risk.sqlite"))
    repo = SqliteRepo()
    geocode.use_cache(repo)
    if args.police:
        load_police(repo)
    if args.once:
        tick(repo)
        return

    def loop() -> None:
        while True:
            tick(repo)
            time.sleep(TICK_S)

    threading.Thread(target=loop, daemon=True).start()

    import uvicorn

    from .api import web

    uvicorn.run(web, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
