"""Modal app: dispatcher cron, per-source polls, rescore cron, HTTP API.

  modal run -m backend.app::init_db                       apply schema
  modal run -m backend.app::poll_source --source-id tfl_road
  modal run -m backend.app::rescore
  modal serve -m backend.app                              API with live reload
  modal deploy -m backend.app                             API + crons
"""

from __future__ import annotations

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("backend/requirements.txt")
    .add_local_python_source("backend")
    .add_local_dir("backend/sql", "/root/backend/sql")
)

app = modal.App("london-risk", image=image, secrets=[modal.Secret.from_name("london-risk")])


@app.function()
def init_db() -> None:
    from . import db

    db.apply_schema()


@app.function(timeout=300, max_containers=20)
def poll_source(source_id: str) -> dict[str, int]:
    from .db import PgRepo
    from .pipeline import SOURCES, run_poll

    counts = run_poll(SOURCES[source_id], PgRepo())
    print(source_id, counts)
    return counts


@app.function(schedule=modal.Cron("* * * * *"))
def dispatcher() -> None:
    from .db import PgRepo
    from .models import utcnow
    from .pipeline import SOURCES

    for source_id in PgRepo().due_sources(utcnow()):
        if source_id in SOURCES:
            poll_source.spawn(source_id)


@app.function(schedule=modal.Cron("* * * * *"), timeout=120)
def rescore() -> int:
    from .db import PgRepo
    from .pipeline import run_rescore

    n = run_rescore(PgRepo())
    print("cells written:", n)
    return n


@app.function(min_containers=0, max_containers=4)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def api():
    from .api import web

    return web
