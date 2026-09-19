"""Platform endpoints used by the client applications (apps/*)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from . import area, db, places
from .models import LONDON_BBOX, in_london, utcnow
from .scoring import FINE_RES, distance_m

router = APIRouter()

_M_PER_DEG_LAT = 111_320.0
_CACHE_S = 60.0
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, load) -> Any:
    """Small time-based cache: the full cell and crime tables are read at most once a minute."""
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < _CACHE_S:
            return hit[1]
    value = load()
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _cell_table() -> tuple[dict[str, dict[str, float]], list[float]]:
    cells = db.cell_scores(FINE_RES, 0.0)
    table = {c.h3: {"score": c.score, "live": c.live, "baseline": c.baseline} for c in cells}
    return table, sorted(c.score for c in cells)


def _crime_rows() -> tuple[str | None, list[list[Any]]]:
    payload = db.latest_crime_points_json()
    if payload is None:
        return None, []
    data = json.loads(payload)
    return data.get("month"), data.get("rows", [])


@router.get("/api/area")
def get_area(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_m: float = Query(400, ge=50, le=3000),
) -> dict[str, Any]:
    """What the platform holds for a circle: modelled risk, recorded crime, current events."""
    if not in_london(lng, lat):
        w, s, e, n = LONDON_BBOX
        raise HTTPException(422, f"point is outside Greater London (lng {w}..{e}, lat {s}..{n})")
    now = utcnow()
    table, all_sorted = _cached("cells", _cell_table)
    month, rows = _cached("crime", _crime_rows)

    pad_lat = (radius_m + 3000) / _M_PER_DEG_LAT
    pad_lng = pad_lat / 0.6225  # cos(51.5 deg)
    events = db.events_geojson(now, (lng - pad_lng, lat - pad_lat, lng + pad_lng, lat + pad_lat))
    return {
        "center": [lng, lat],
        "radius_m": radius_m,
        "generated_at": now.isoformat(),
        "risk": area.risk_summary(area.cells_within(lng, lat, radius_m), table, all_sorted),
        "crime": area.crime_summary(rows, month, lng, lat, radius_m),
        "events": area.events_within(events, lng, lat, radius_m, now),
    }


HOTEL_AREA_RADIUS_M = 300.0
MAX_HOTELS = 60


def _bbox_around(lng: float, lat: float, radius_m: float) -> tuple[float, float, float, float]:
    pad_lat = radius_m / _M_PER_DEG_LAT
    pad_lng = pad_lat / 0.6225  # cos(51.5 deg)
    return lng - pad_lng, lat - pad_lat, lng + pad_lng, lat + pad_lat


@router.get("/api/hotels")
def get_hotels(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    radius_m: float = Query(1500, ge=100, le=5000),
    sort: str = Query("safety", pattern="^(safety|distance)$"),
) -> dict[str, Any]:
    """OpenStreetMap hotels around a point. Each has the modelled risk of its
    surroundings (the /api/area risk within 300 m) and its nearest rail station,
    so a client can request the walking route between the two from /api/route."""
    if not in_london(lng, lat):
        raise HTTPException(422, "point is outside Greater London")
    if places.count("hotel") == 0:
        raise HTTPException(503, "no hotel data loaded; run the refresh_places job")
    table, all_sorted = _cached("cells", _cell_table)
    hotels = []
    for h in places.within("hotel", _bbox_around(lng, lat, radius_m)):
        d = distance_m(lng, lat, h["lng"], h["lat"])
        if d > radius_m:
            continue
        risk = area.risk_summary(area.cells_within(h["lng"], h["lat"], HOTEL_AREA_RADIUS_M), table, all_sorted)
        stations = places.within("station", _bbox_around(h["lng"], h["lat"], 1500))
        nearest = min(stations, key=lambda s: distance_m(h["lng"], h["lat"], s["lng"], s["lat"]), default=None)
        hotels.append(
            h
            | {
                "distance_m": round(d),
                "risk": risk,
                "nearest_station": nearest
                and {
                    "name": nearest["name"],
                    "lng": nearest["lng"],
                    "lat": nearest["lat"],
                    "distance_m": round(distance_m(h["lng"], h["lat"], nearest["lng"], nearest["lat"])),
                },
            }
        )
    key = (lambda h: h["risk"]["mean_score"]) if sort == "safety" else (lambda h: h["distance_m"])
    hotels.sort(key=key)
    return {
        "center": [lng, lat],
        "radius_m": radius_m,
        "total": len(hotels),
        "hotels": hotels[:MAX_HOTELS],
        "attribution": "Hotel and station data (c) OpenStreetMap contributors",
    }
