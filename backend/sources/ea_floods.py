"""Environment Agency flood warnings and alerts within 30 km of central London."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

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

BASE = "https://environment.data.gov.uk/flood-monitoring/id"
URL = f"{BASE}/floods"
PARAMS = {"lat": "51.5", "long": "-0.12", "dist": "30"}

# severityLevel: 1 = Severe Flood Warning, 2 = Flood Warning, 3 = Flood Alert.
# Level 4 (warning no longer in force) has no entry and produces no event.
SEVERITY = {
    1: 0.9,
    2: 0.6,
    3: 0.3,
}

# Tolerance in degrees (about 35 m east-west at London's latitude). Unsimplified
# flood-area polygons are several hundred kilobytes each.
SIMPLIFY_TOLERANCE = 0.0005

# floodAreaID -> {"geometry": GeoJSON, "lng": float, "lat": float}. Flood area
# boundaries are static, so entries are kept for the lifetime of the process.
# Only results that include the polygon are cached; failures are retried on the
# next poll.
_AREA_CACHE: dict[str, dict[str, Any]] = {}


class EaFloodsSource:
    id = "ea_floods"
    snapshot = True
    # No warnings in force is the usual state of this feed
    empty_is_valid = True

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        resp = httpx.get(URL, params=PARAMS, timeout=30)
        resp.raise_for_status()
        items = []
        for d in resp.json()["items"]:
            area_id = d["floodAreaID"]
            # `area` is added to the upstream payload so that to_event needs no
            # network access. It is omitted for items that produce no event.
            if d.get("severityLevel") in SEVERITY:
                d["area"] = _area(area_id)
            items.append(RawItem(source_id=self.id, external_id=area_id, payload=d))
        return items, cursor

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        severity = SEVERITY.get(d.get("severityLevel"))
        if severity is None:  # no longer in force, and unknown values
            return None

        area = d.get("area") or {}
        lng, lat = area.get("lng"), area.get("lat")
        if lng is None or lat is None or not in_london(lng, lat):
            return None

        # Flood-area polygon when it was fetched and is valid, otherwise the
        # area's centroid.
        geometry: dict[str, Any] = {"type": "Point", "coordinates": [lng, lat]}
        if (
            (g := area.get("geometry"))
            and g.get("type") in ("Polygon", "MultiPolygon")
            and shape(g).is_valid
        ):
            geometry = {"type": g["type"], "coordinates": g["coordinates"]}

        confidence = SOURCE_TYPE_CONFIDENCE["official_feed"]
        return Event(
            external_ref=external_ref(raw),
            category=Category.FLOOD,
            title=f"Flood warning: {d.get('description', '').strip()}"[:200],
            summary=d.get("message"),
            geometry=geometry,
            lng=lng,
            lat=lat,
            radius_m=CATEGORY_DEFAULTS[Category.FLOOD].radius_m,
            severity=severity,
            confidence=confidence,
            source_confidence={self.id: confidence},
            half_life_min=None,
            occurred_at=_parse_dt(d.get("timeRaised")) or utcnow(),
            source_ids=[self.id],
            urls=[d["@id"]] if d.get("@id") else [],
        )


def _area(area_id: str) -> dict[str, Any]:
    """Simplified polygon and centroid of one flood area.

    The result has no "geometry" key when the polygon request fails, and no
    "lng"/"lat" keys when both requests fail.
    """
    if area_id in _AREA_CACHE:
        return _AREA_CACHE[area_id]

    area: dict[str, Any] = {}
    try:
        resp = httpx.get(f"{BASE}/floodAreas/{area_id}", timeout=30)
        resp.raise_for_status()
        info = resp.json()["items"]
        area["lng"], area["lat"] = float(info["long"]), float(info["lat"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        pass

    try:
        resp = httpx.get(
            f"{BASE}/floodAreas/{area_id}/polygon", timeout=60, follow_redirects=True
        )
        resp.raise_for_status()
        geom = _simplified(resp.json())
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return area

    area["geometry"] = mapping(geom)
    if "lng" not in area:
        area["lng"], area["lat"] = geom.centroid.x, geom.centroid.y
    _AREA_CACHE[area_id] = area
    return area


def _simplified(feature_collection: dict[str, Any]):
    """Union of the collection's features, simplified. Raises ValueError when the
    result is not a non-empty valid polygon or multipolygon."""
    geom = unary_union([shape(f["geometry"]) for f in feature_collection["features"]])
    geom = geom.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
    if geom.is_empty or not geom.is_valid or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"unusable flood area geometry: {geom.geom_type}")
    return geom


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # The API returns ISO strings without an offset. They are treated as UTC;
    # during British Summer Time this can be off by one hour.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
