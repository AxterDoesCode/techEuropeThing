from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .models import Category, CellScore, Event, utcnow
from .scoring import COARSE_RES, FINE_RES, MIN_EVENT_RISK, event_risk

web = FastAPI(title="London Live Risk Map API")
web.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(422, "bbox must be 'west,south,east,north'")
    return w, s, e, n


def _feature(ev: Event, now: datetime) -> dict[str, Any]:
    props = ev.model_dump(mode="json", exclude={"geometry", "source_confidence", "raw_item_ids"})
    props["risk"] = round(event_risk(ev, now), 4)
    return {"type": "Feature", "id": str(ev.id), "geometry": ev.geometry, "properties": props}


@web.get("/api/events")
def get_events(
    bbox: str | None = None,
    since: datetime | None = None,
    category: Category | None = None,
    active: bool = True,
) -> dict[str, Any]:
    now = utcnow()
    events = db.events_geojson(now, _parse_bbox(bbox), since, category.value if category else None)
    features = [_feature(ev, now) for ev in events]
    if active:
        features = [f for f in features if f["properties"]["risk"] >= MIN_EVENT_RISK]
    return {"type": "FeatureCollection", "features": features}


@web.get("/api/events/{event_id}")
def get_event(event_id: UUID) -> dict[str, Any]:
    ev = db.event_by_id(event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    return _feature(ev, utcnow())


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
