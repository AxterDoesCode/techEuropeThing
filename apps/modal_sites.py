"""Static hosting of the client applications on Modal, separate from the platform app.

  cd apps/<name> && npm install && npm run build     for each app to publish
  modal deploy apps/modal_sites.py

Each site is one web function that serves apps/<name>/dist. `console` serves
web/dist, the live map with the data source status. A site whose dist directory
was not built answers with a short notice.
"""

from __future__ import annotations

from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent
SITES = {
    "navigate": ROOT / "navigate" / "dist",
    "stay": ROOT / "stay" / "dist",
    "ask": ROOT / "ask" / "dist",
    "console": ROOT.parent / "web" / "dist",
}

image = modal.Image.debian_slim(python_version="3.12").pip_install("fastapi[standard]>=0.110")
for _name, _dist in SITES.items():
    if _dist.is_dir():
        image = image.add_local_dir(str(_dist), f"/sites/{_name}")

app = modal.App("london-risk-apps", image=image)


def _site(name: str):
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from fastapi.staticfiles import StaticFiles

    web = FastAPI(title=f"london-risk {name}")
    directory = Path(f"/sites/{name}")
    if directory.is_dir():
        web.mount("/", StaticFiles(directory=directory, html=True), name=name)
    else:

        @web.get("/")
        def not_built() -> PlainTextResponse:
            return PlainTextResponse(f"The {name} app has not been built and deployed yet.", status_code=503)

    return web


@app.function(max_containers=2)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def navigate():
    return _site("navigate")


@app.function(max_containers=2)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def stay():
    return _site("stay")


@app.function(max_containers=2)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def ask():
    return _site("ask")


@app.function(max_containers=2)
@modal.concurrent(max_inputs=100)
@modal.asgi_app()
def console():
    return _site("console")
