"""POST /api/route. Include in the app with `web.include_router(api_route.router)`."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import db, routing
from .scoring import FINE_RES

router = APIRouter()

SCORE_PAD_M = 1500.0
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LNG = 69_300.0  # at latitude 51.5

_graph: routing.Graph | None = None
_graph_path: str | None = None
_graph_lock = threading.Lock()


def graph_path() -> str:
    return os.environ.get("GRAPH_PATH", "data/graph/walk.npz")


def get_graph() -> routing.Graph:
    """Loaded once per process; reloaded only when GRAPH_PATH changes."""
    global _graph, _graph_path
    path = graph_path()
    with _graph_lock:
        if _graph is None or _graph_path != path:
            if not Path(path).is_file():
                raise HTTPException(
                    503,
                    f"routing graph not found at {path!r}; build it with"
                    " `python -m backend.tools.build_graph` or set GRAPH_PATH",
                )
            _graph = routing.load_graph(path)
            _graph_path = path
        return _graph


class RouteRequest(BaseModel):
    origin: tuple[float, float] = Field(description="[lng, lat]")
    destination: tuple[float, float] = Field(description="[lng, lat]")
    alpha: float = Field(routing.DEFAULT_ALPHA, ge=0, le=10)

    @field_validator("origin", "destination")
    @classmethod
    def _lng_lat(cls, p: tuple[float, float]) -> tuple[float, float]:
        if not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            raise ValueError("expected [lng, lat] in degrees")
        return p


def _scores_near(origin: tuple[float, float], destination: tuple[float, float]) -> dict[str, float]:
    pad_lng, pad_lat = SCORE_PAD_M / _M_PER_DEG_LNG, SCORE_PAD_M / _M_PER_DEG_LAT
    bbox = (
        min(origin[0], destination[0]) - pad_lng,
        min(origin[1], destination[1]) - pad_lat,
        max(origin[0], destination[0]) + pad_lng,
        max(origin[1], destination[1]) + pad_lat,
    )
    return {c.h3: c.score for c in db.cell_scores(FINE_RES, 0.0, bbox)}


@router.post("/api/route")
def post_route(req: RouteRequest) -> dict[str, Any]:
    graph = get_graph()
    try:
        return routing.route(graph, req.origin, req.destination, _scores_near(req.origin, req.destination), req.alpha)
    except routing.RouteError as exc:
        raise HTTPException(422, str(exc))
