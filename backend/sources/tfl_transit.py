"""TfL rail station disruptions: closures, part closures, access restrictions.

Uses the station-level feed `StopPoint/Mode/{modes}/Disruption` rather than
`Line/Mode/{modes}/Status`. Each station-level item names one stop (atcoCode),
so it maps to one point event. Line status items describe a whole line or a
section between two named stations and carry no usable geometry.

The disruption feed has no coordinates. fetch() resolves each atcoCode through
`StopPoint/{ids}` and writes `lat`/`lon` into the payload, so to_event() reads
only the payload. Resolved coordinates are cached for the process lifetime.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from datetime import datetime
from typing import Any, Iterator

import httpx

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

MODES = "tube,overground,dlr,elizabeth-line"
URL = f"https://api.tfl.gov.uk/StopPoint/Mode/{MODES}/Disruption"
STOP_URL = "https://api.tfl.gov.uk/StopPoint/{ids}"
# Longer id lists make the request URL exceed the upstream length limit (HTTP 400).
STOP_BATCH = 20

# Keyed by the upstream `type`. "Part Closure" covers trains not calling in one
# direction and no service on the line section through the station.
# "Interchange Message" and "Information" are lift, escalator, entrance and
# step-free access notices.
SEVERITY = {
    "Closure": 0.6,
    "Part Closure": 0.4,
    "Exit Only": 0.3,
    "Interchange Message": 0.15,
    "Information": 0.1,
}

# appearance "Information" marks standing accessibility advice with end dates
# months or years ahead (platform gaps, staff assistance), not a disruption.
SKIPPED_APPEARANCES = {"Information"}

# atcoCode -> (lat, lon). None when upstream does not know the stop.
_STOP_COORDS: dict[str, tuple[float, float] | None] = {}


class TflTransitSource:
    id = "tfl_transit"
    snapshot = True

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        params = _params()
        resp = httpx.get(URL, params=params, timeout=30)
        resp.raise_for_status()
        disruptions = resp.json()

        _resolve_stops({d["atcoCode"] for d in disruptions if d.get("atcoCode")}, params)
        return self.items(disruptions), cursor

    def items(self, disruptions: list[dict[str, Any]]) -> list[RawItem]:
        """Embed cached coordinates and assign external ids."""
        base_ids = [_base_id(d) for d in disruptions]
        counts = Counter(base_ids)
        items = []
        for d, base in zip(disruptions, base_ids):
            if coords := _STOP_COORDS.get(d.get("atcoCode", "")):
                d = {**d, "lat": coords[0], "lon": coords[1]}
            # Two disruptions at one stop with equal type and start are told
            # apart by a hash of the description.
            ext = base if counts[base] == 1 else f"{base}:{_digest(d.get('description', ''))}"
            items.append(RawItem(source_id=self.id, external_id=ext, payload=d))
        return items

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        if d.get("appearance") in SKIPPED_APPEARANCES:
            return None
        severity = SEVERITY.get(d.get("type", ""))
        if severity is None:  # unknown types
            return None

        lng, lat = d.get("lon"), d.get("lat")
        if lng is None or lat is None or not in_london(lng, lat):
            return None

        category = Category.TRANSIT_DISRUPTION
        confidence = SOURCE_TYPE_CONFIDENCE["official_feed"]
        name = (d.get("commonName") or d.get("atcoCode") or "").strip()
        return Event(
            external_ref=external_ref(raw),
            category=category,
            title=f"{d['type']}: {name}"[:200],
            summary=d.get("description"),
            geometry={"type": "Point", "coordinates": [lng, lat]},
            lng=lng,
            lat=lat,
            radius_m=CATEGORY_DEFAULTS[category].radius_m,
            severity=severity,
            confidence=confidence,
            source_confidence={self.id: confidence},
            half_life_min=None,
            occurred_at=_parse_dt(d.get("fromDate")) or utcnow(),
            expires_at=_parse_dt(d.get("toDate")),
            source_ids=[self.id],
            urls=[],
        )


def _params() -> dict[str, str]:
    params = {}
    if key := os.environ.get("TFL_APP_KEY"):
        params["app_key"] = key
    return params


def _base_id(d: dict[str, Any]) -> str:
    stop = d.get("atcoCode") or d.get("stationAtcoCode")
    if not stop:
        return _digest(d.get("description", ""))
    return f"{stop}:{d.get('type', '')}:{d.get('fromDate', '')}"


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _resolve_stops(atco_codes: set[str], params: dict[str, str]) -> None:
    """Fill _STOP_COORDS for every code not already cached."""
    missing = sorted(atco_codes - _STOP_COORDS.keys())
    for i in range(0, len(missing), STOP_BATCH):
        batch = missing[i : i + STOP_BATCH]
        found = _lookup(batch, params)
        for code in batch:
            if code in found:
                _STOP_COORDS[code] = found[code]
    # A batch response replaces a station that belongs to an interchange hub
    # with the hub record, and not every station is listed among the hub's
    # children. A single lookup returns the same hub, whose own coordinates
    # are then used.
    for code in missing:
        if code not in _STOP_COORDS:
            found = _lookup([code], params)
            _STOP_COORDS[code] = found.get(code) or found.get("")


def _lookup(codes: list[str], params: dict[str, str]) -> dict[str, tuple[float, float]]:
    """Coordinates by stop id for every stop in the response tree.

    For a single-code request the key "" holds the top-level record's
    coordinates. An unknown code (HTTP 404) gives an empty result.
    """
    resp = httpx.get(STOP_URL.format(ids=",".join(codes)), params=params, timeout=30)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    body = resp.json()
    found: dict[str, tuple[float, float]] = {}
    if isinstance(body, dict):
        if body.get("lat") and body.get("lon"):
            found[""] = (body["lat"], body["lon"])
        body = [body]
    for stop in _walk(body):
        if stop.get("lat") and stop.get("lon"):
            found.setdefault(stop.get("naptanId") or stop.get("id"), (stop["lat"], stop["lon"]))
    return found


def _walk(stops: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for stop in stops:
        yield stop
        yield from _walk(stop.get("children") or [])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
