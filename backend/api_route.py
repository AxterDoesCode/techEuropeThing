"""POST /api/route. Include in the app with `web.include_router(api_route.router)`."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from . import db, routing
from .scoring import FINE_RES

router = APIRouter()


_graph: routing.Graph | None = None
_graph_path: str | None = None
_graph_lock = threading.Lock()
# Street-level crime baseline for the loaded graph, keyed by the crime data month
_baseline: tuple[str, str, Any] | None = None


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
    depart_at: datetime | None = Field(
        None,
        description="ISO 8601 departure time, used for the night multiplier on the crime"
        " baseline; without a UTC offset it is read as London local time. Default: now.",
    )

    @field_validator("origin", "destination")
    @classmethod
    def _lng_lat(cls, p: tuple[float, float]) -> tuple[float, float]:
        if not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            raise ValueError("expected [lng, lat] in degrees")
        return p


def _live_scores(graph: routing.Graph) -> dict[str, float]:
    """Live component of every cell in the graph area. The crime baseline is
    applied per street, not per cell. The whole area is read, not a window around
    the two points: cells outside a window would count as risk 0 and the safe
    route would be drawn out of the window through them."""
    return {c.h3: c.live for c in db.cell_scores(FINE_RES, 0.0, graph.bbox) if c.live > 0}


def get_baseline(graph: routing.Graph) -> Any:
    """Per-edge crime baseline; recomputed when the graph or the crime month changes.
    None when no crime data is loaded."""
    global _baseline
    month = db.latest_crime_month()
    if month is None:
        return None
    with _graph_lock:
        if _baseline is None or _baseline[:2] != (graph_path(), month):
            payload = json.loads(db.latest_crime_points_json() or "{}")
            _baseline = (graph_path(), month, routing.edge_baseline(graph, payload.get("rows", [])))
        return _baseline[2]


def warm_up() -> None:
    """Load the graph and compute the baseline ahead of the first request."""
    if Path(graph_path()).is_file():
        get_baseline(get_graph())


@router.post("/api/route")
def post_route(req: RouteRequest) -> dict[str, Any]:
    graph = get_graph()
    try:
        return routing.route(
            graph,
            req.origin,
            req.destination,
            _live_scores(graph),
            req.alpha,
            baseline=get_baseline(graph),
            depart_at=req.depart_at or datetime.now(routing.LONDON_TZ),
        )
    except routing.RouteError as exc:
        raise HTTPException(422, str(exc))
