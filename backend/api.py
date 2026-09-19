from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .api_alerts import router as alerts_router
from .api_route import router as route_router
from .api_stream import router as stream_router
from .features import event_feature
from .models import Category, CellScore, utcnow
from .scoring import COARSE_RES, FINE_RES, MIN_EVENT_RISK

web = FastAPI(title="London Live Risk Map API")
web.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
web.add_middleware(GZipMiddleware, minimum_size=10_000)
web.include_router(route_router)
web.include_router(stream_router)
web.include_router(alerts_router)


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox must be 'west,south,east,north'")
    return w, s, e, n


@web.get("/api/events")
def get_events(
    bbox: str | None = None,
    since: datetime | None = None,
    category: Category | None = None,
    active: bool = True,
) -> dict[str, Any]:
    now = utcnow()
    events = db.events_geojson(now, _parse_bbox(bbox), since, category.value if category else None)
    features = [event_feature(ev, now) for ev in events]
    if active:
        features = [f for f in features if f["properties"]["risk"] >= MIN_EVENT_RISK]
    return {"type": "FeatureCollection", "features": features}


@web.get("/api/events/{event_id}")
def get_event(event_id: UUID) -> dict[str, Any]:
    ev = db.event_by_id(event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    return event_feature(ev, utcnow())


@web.get("/api/cells")
def get_cells(
    res: int = Query(FINE_RES, description=f"{FINE_RES} or {COARSE_RES}"),
    bbox: str | None = None,
    min_score: float = Query(0.05, ge=0, le=1),
) -> list[CellScore]:
    if res not in (FINE_RES, COARSE_RES):
        raise HTTPException(422, f"res must be {FINE_RES} or {COARSE_RES}")
    return db.cell_scores(res, min_score, _parse_bbox(bbox))


@web.get("/api/agents")
def get_agents() -> list[dict[str, Any]]:
    return db.agent_status()


@web.get("/api/crime-points")
def get_crime_points() -> Response:
    """Latest month of Metropolitan Police street-level crime, one row per street point."""
    payload = db.latest_crime_points_json()
    if payload is None:
        raise HTTPException(404, "no crime data loaded; run backfill_police")
    # police.uk publishes monthly
    return Response(payload, media_type="application/json", headers={"Cache-Control": "public, max-age=3600"})
