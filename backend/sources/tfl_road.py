"""TfL road disruptions: works, closures, collisions and planned events."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

import httpx
from shapely.geometry import shape

from ..models import (
    CATEGORY_DEFAULTS,
    SOURCE_TYPE_CONFIDENCE,
    Category,
    Event,
    RawItem,
    in_london,
    utcnow,
)
from .base import external_ref

URL = "https://api.tfl.gov.uk/Road/all/Disruption"

SEVERITY = {
    "Minimal": 0.1,
    "Moderate": 0.3,
    "Serious": 0.55,
    "Severe": 0.8,
}

SUBCATEGORY_TO_CATEGORY = {
    "Demonstration/March": Category.DISORDER,
}


class TflRoadSource:
    id = "tfl_road"
    snapshot = True

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        params = {"stripContent": "false"}
        if key := os.environ.get("TFL_APP_KEY"):
            params["app_key"] = key
        resp = httpx.get(URL, params=params, timeout=30)
        resp.raise_for_status()
        items = [
            RawItem(source_id=self.id, external_id=d["id"], payload=d) for d in resp.json()
        ]
        return items, cursor

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        severity = SEVERITY.get(d.get("severity", ""))
        if severity is None:  # "No impact" and unknown values
            return None

        lng, lat = json.loads(d["point"])
        if not in_london(lng, lat):
            return None

        # Most specific geometry available: affected street segments, then the
        # works-area polygon, then the single point.
        geometry: dict[str, Any] = {"type": "Point", "coordinates": [lng, lat]}
        if lines := _street_lines(d):
            geometry = {"type": "MultiLineString", "coordinates": lines}
        elif (g := d.get("geometry")) and shape(g).is_valid:
            geometry = {"type": g["type"], "coordinates": g["coordinates"]}

        category = SUBCATEGORY_TO_CATEGORY.get(d.get("subCategory", ""), Category.ROAD_CLOSURE)
        confidence = SOURCE_TYPE_CONFIDENCE["official_feed"]
        sub = d.get("subCategory") or d.get("category") or "Disruption"
        return Event(
            external_ref=external_ref(raw),
            category=category,
            title=f"{sub}: {d.get('location', '').strip()}"[:200],
            summary=d.get("currentUpdate") or d.get("comments"),
            geometry=geometry,
            lng=lng,
            lat=lat,
            radius_m=CATEGORY_DEFAULTS[category].radius_m,
            severity=severity,
            confidence=confidence,
            source_confidence={self.id: confidence},
            half_life_min=None,
            occurred_at=_parse_dt(d.get("startDateTime")) or utcnow(),
            expires_at=_parse_dt(d.get("endDateTime")),
            source_ids=[self.id],
            urls=[f"https://api.tfl.gov.uk{d['url']}"] if d.get("url") else [],
        )


def _street_lines(d: dict[str, Any]) -> list[list[list[float]]]:
    lines = []
    for street in d.get("streets") or []:
        for seg in street.get("segments") or []:
            coords = json.loads(seg.get("lineString") or "[]")
            if len(coords) >= 2:
                lines.append(coords)
    return lines


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
