"""TfL road incidents: collisions, emergency-service incidents, hazards and demonstrations.

Product rule (owner): the map covers pedestrian safety only. "Only consider
events that could have an effect on the user route." Road incidents are kept;
roadworks of every kind, asset faults, traffic-delay notices, vehicle breakdowns
and planned events other than demonstrations and marches (sporting, concert,
exhibition, ceremonial, filming, ...) are convenience information and produce
no event.
"""

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

# Owner's rule: keep incidents relevant to a person on foot (collisions,
# emergency-service incidents, hazards, weather hazards, demonstrations and
# marches); drop convenience information.
#
# Kept: ANY subCategory under the upstream categories in KEPT_CATEGORIES, plus the
# pair Planned events / Demonstration/March. An unknown subCategory under a kept
# category is kept, because the category already identifies an incident. Every
# other category is dropped, including categories added upstream later.
#
# Observed and dropped: Works/* (Borough, Collaborative, Construction activity,
# Motorways, Network Rail, TfL, Utility works), Asset issues/* (Asset fault,
# Asset maintenance), Network delays/* (Heavy traffic, Service disruption),
# Breakdowns/Vehicle breakdown, and Planned events/* other than
# Demonstration/March (Abnormal load, Celebration, Ceremonial, Commemoration,
# Concert, Exhibition, Filming, Security barriers (HVM), Shopping, Sporting).
#
# The values are those observed in the feed (date-range queries for 2025-01 to
# 2027-03, run on 2026-09-19). TfL does not document them: Road/Meta/Categories
# and the TIMS feed specification list an older vocabulary ("Hazard(s)",
# "Traffic Incidents", ...) that the feed's `category` field does not use.
#
# Upstream `severity` is ignored. It rates the traffic delay, not the danger to
# a person on foot: a road closed for police activity has been observed with
# severity "No impact".
#
# No Category value describes a collision or an emergency-service incident;
# ROAD_CLOSURE is the closest existing value (group "road").
#
# REVISIT(severity-scale): the severities are provisional values.
#
# upstream category -> (event category, severity) for any subCategory
KEPT_CATEGORIES: dict[str, tuple[Category, float]] = {
    "Collisions": (Category.ROAD_CLOSURE, 0.4),
    "Emergency service incidents": (Category.ROAD_CLOSURE, 0.4),
    "Hazards": (Category.ROAD_CLOSURE, 0.4),
    # Not Category.WEATHER: its default radius (2000 m) describes an area-wide
    # weather warning, not a hazard on one road.
    "Weather": (Category.ROAD_CLOSURE, 0.4),
}
# (upstream category, subCategory) -> (event category, severity). Takes precedence
# over KEPT_CATEGORIES.
KEPT_PAIRS: dict[tuple[str, str], tuple[Category, float]] = {
    ("Hazards", "Fire"): (Category.FIRE, 0.6),
    ("Weather", "Flooding"): (Category.FLOOD, 0.4),
    ("Planned events", "Demonstration/March"): (Category.DISORDER, 0.35),
}


def classify(d: dict[str, Any]) -> tuple[Category, float] | None:
    """Event category and severity of a feed item. None when the item is dropped."""
    category = d.get("category") or ""
    return KEPT_PAIRS.get((category, d.get("subCategory") or "")) or KEPT_CATEGORIES.get(category)


class TflRoadSource:
    id = "tfl_road"
    snapshot = True

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        params = {"stripContent": "false"}
        if key := os.environ.get("TFL_APP_KEY"):
            params["app_key"] = key
        resp = httpx.get(URL, params=params, timeout=30)
        resp.raise_for_status()
        # Dropped items are returned too and are filtered in to_event(). run_poll
        # skips end_missing when a snapshot fetch returns no items, so a result
        # reduced to the kept items would be empty whenever no incident is
        # listed, and the stored incidents would then never be marked ended.
        items = [
            RawItem(source_id=self.id, external_id=d["id"], payload=d) for d in resp.json()
        ]
        return items, cursor

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        kept = classify(d)
        if kept is None:
            return None
        category, severity = kept

        lng, lat = json.loads(d["point"])
        if not in_london(lng, lat):
            return None

        # Most specific geometry available: affected street segments, then the
        # disruption-area polygon, then the single point.
        geometry: dict[str, Any] = {"type": "Point", "coordinates": [lng, lat]}
        if lines := _street_lines(d):
            geometry = {"type": "MultiLineString", "coordinates": lines}
        elif (g := d.get("geometry")) and shape(g).is_valid:
            geometry = {"type": g["type"], "coordinates": g["coordinates"]}

        confidence = SOURCE_TYPE_CONFIDENCE["official_feed"]
        sub = d.get("subCategory") or d["category"]
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
