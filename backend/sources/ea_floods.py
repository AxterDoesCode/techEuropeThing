"""Environment Agency flood warnings and alerts whose flood area reaches the London bbox."""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any

import httpx
from shapely import make_valid
from shapely.errors import ShapelyError
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

from ..models import (
    CATEGORY_DEFAULTS,
    SOURCE_TYPE_CONFIDENCE,
    Category,
    LONDON_BBOX,
    Event,
    RawItem,
    in_london,
    utcnow,
)
from .base import external_ref

log = logging.getLogger(__name__)

BASE = "https://environment.data.gov.uk/flood-monitoring/id"
URL = f"{BASE}/floods"

# Added to the largest centre-to-corner distance of the bbox. The API's lat/long/
# dist filter is applied to flood areas, not to their polygons, so an area whose
# centroid is outside the bbox is only returned when the radius reaches it.
# Measured on 2026-09-19 with /id/floodAreas: 28 areas with a centroid outside the
# bbox have a polygon that intersects it (277 of 339 such areas within dist=60
# were checked); the farthest centroid is 41.6 km from the bbox centre. The filter
# is approximate: dist=60 also returned areas whose centroid is up to 94 km away.
# Areas returned: 356 at dist=37, 409 at dist=45.
QUERY_MARGIN_KM = 8


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2
    )
    return 12742 * math.asin(math.sqrt(a))


def _query_params() -> dict[str, str]:
    """Centre of LONDON_BBOX and a radius that covers its corners plus the margin."""
    w, s, e, n = LONDON_BBOX
    lat, lng = (s + n) / 2, (w + e) / 2
    corner_km = max(_haversine_km(lat, lng, y, x) for y in (s, n) for x in (w, e))
    dist = math.ceil(corner_km) + QUERY_MARGIN_KM
    return {"lat": f"{lat:.5f}", "long": f"{lng:.5f}", "dist": str(dist)}


# lat 51.48935, long -0.08820, dist 45 (corner distance 36.95 km)
PARAMS = _query_params()
LONDON_BOX = box(*LONDON_BBOX)

# severityLevel: 1 = Severe Flood Warning, 2 = Flood Warning, 3 = Flood Alert.
# Level 4 (warning no longer in force) has no entry and produces no event.
SEVERITY = {
    1: 0.9,
    2: 0.6,
    3: 0.3,
}
# Title prefix when the payload has no `severity` text
SEVERITY_LABEL = {
    1: "Severe flood warning",
    2: "Flood warning",
    3: "Flood alert",
}

# Flood-area requests. The API gateway blocked an IP address with HTTP 403 on all
# endpoints after roughly 280 requests in a few minutes (2026-09-19), so the
# number of requests per poll is bounded. A poll has a hard limit of 280 s.
AREA_WORKERS = 4
REQUEST_TIMEOUT_S = 20
# Requests not started within this time are skipped; the area is looked up again
# on the next poll.
AREA_BUDGET_S = 120
# Uncached flood areas looked up per poll (two requests each)
MAX_AREA_LOOKUPS = 60

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
        resp = httpx.get(URL, params=PARAMS, timeout=REQUEST_TIMEOUT_S)
        # Any error status, including 403 and 429 from the gateway, fails the poll.
        # Returning an empty list instead would end every active event, because
        # an empty result is valid for this source.
        resp.raise_for_status()
        payloads = resp.json()["items"]
        # Most severe first, so that the lookup cap drops the least severe areas
        in_force = sorted(
            (d for d in payloads if d.get("severityLevel") in SEVERITY),
            key=lambda d: (d["severityLevel"], d["floodAreaID"]),
        )
        areas = _areas(list(dict.fromkeys(d["floodAreaID"] for d in in_force)))
        items = []
        for d in payloads:
            area_id = d["floodAreaID"]
            # `area` is added to the upstream payload so that to_event needs no
            # network access. It is omitted for items that produce no event.
            if d.get("severityLevel") in SEVERITY:
                d["area"] = areas.get(area_id, {})
            items.append(RawItem(source_id=self.id, external_id=area_id, payload=d))
        return items, cursor

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        severity = SEVERITY.get(d.get("severityLevel"))
        if severity is None:  # no longer in force, and unknown values
            return None

        area = d.get("area") or {}
        lng, lat = area.get("lng"), area.get("lat")
        if lng is None or lat is None:
            return None

        # Flood-area polygon when it was fetched and is valid, otherwise the
        # area's centroid. The warning is kept when the polygon intersects the
        # London bbox; a large area can have its centroid outside the bbox. The
        # centroid is tested only when there is no polygon.
        polygon = None
        if (g := area.get("geometry")) and g.get("type") in ("Polygon", "MultiPolygon"):
            try:
                polygon = shape(g)
            except (ShapelyError, KeyError, TypeError, ValueError):
                polygon = None
            if polygon is not None and (polygon.is_empty or not polygon.is_valid):
                polygon = None
        if polygon is not None:
            if not polygon.intersects(LONDON_BOX):
                return None
            geometry: dict[str, Any] = {"type": g["type"], "coordinates": g["coordinates"]}
        elif in_london(lng, lat):
            geometry = {"type": "Point", "coordinates": [lng, lat]}
        else:
            return None

        level = d["severityLevel"]
        label = (d.get("severity") or "").strip().capitalize() or SEVERITY_LABEL[level]
        summary = (d.get("message") or "").strip()
        if isinstance(d.get("isTidal"), bool):
            tidal = "Tidal." if d["isTidal"] else "Non-tidal (river or groundwater)."
            summary = f"{tidal} {summary}".strip()
        # timeSeverityChanged is when the current severity came into force.
        # timeRaised is when the warning was last reviewed and moves forward on
        # every review.
        occurred_at = (
            _parse_dt(d.get("timeSeverityChanged")) or _parse_dt(d.get("timeRaised")) or utcnow()
        )

        confidence = SOURCE_TYPE_CONFIDENCE["official_feed"]
        return Event(
            external_ref=external_ref(raw),
            category=Category.FLOOD,
            title=f"{label}: {(d.get('description') or '').strip()}"[:200],
            summary=summary or None,
            geometry=geometry,
            lng=lng,
            lat=lat,
            radius_m=CATEGORY_DEFAULTS[Category.FLOOD].radius_m,
            severity=severity,
            confidence=confidence,
            source_confidence={self.id: confidence},
            half_life_min=None,
            occurred_at=occurred_at,
            source_ids=[self.id],
            urls=[d["@id"]] if d.get("@id") else [],
        )


def _get_json(url: str) -> Any | None:
    """Response body of one flood-area request, or None when the request fails."""
    try:
        resp = httpx.get(url, timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


def _get_all(urls: list[str], budget_s: float = AREA_BUDGET_S) -> dict[str, Any | None]:
    """Fetch the URLs with AREA_WORKERS concurrent requests.

    The value is None for a failed request and for a request that had not
    finished when the budget ran out. Requests not yet started at that point
    are cancelled.
    """
    if not urls:
        return {}
    pool = ThreadPoolExecutor(max_workers=AREA_WORKERS)
    futures = {url: pool.submit(_get_json, url) for url in urls}
    done, _ = wait(futures.values(), timeout=budget_s)
    pool.shutdown(wait=False, cancel_futures=True)
    return {url: f.result() if f in done else None for url, f in futures.items()}


def _areas(area_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Simplified polygon and centroid of each flood area, from the cache where
    possible. At most MAX_AREA_LOOKUPS uncached areas, the first in `area_ids`,
    are requested; the rest are absent from the result.

    An area has no "geometry" key when its polygon request fails or the polygon
    is unusable, and no "lng"/"lat" keys when both of its requests fail.
    """
    missing = [a for a in area_ids if a not in _AREA_CACHE]
    if len(missing) > MAX_AREA_LOOKUPS:
        log.warning(
            "ea_floods: %d uncached flood areas, looking up %d; the rest are retried on the next poll",
            len(missing),
            MAX_AREA_LOOKUPS,
        )
        missing = missing[:MAX_AREA_LOOKUPS]
    started = time.monotonic()
    urls = [f"{BASE}/floodAreas/{a}{suffix}" for a in missing for suffix in ("", "/polygon")]
    bodies = _get_all(urls)
    fetched = {
        a: _build_area(bodies[f"{BASE}/floodAreas/{a}"], bodies[f"{BASE}/floodAreas/{a}/polygon"])
        for a in missing
    }
    if missing:
        log.info(
            "ea_floods: %d flood areas looked up in %.1f s, %d without polygon",
            len(missing),
            time.monotonic() - started,
            sum("geometry" not in area for area in fetched.values()),
        )
    for area_id, area in fetched.items():
        # Only results that include the polygon are cached
        if "geometry" in area:
            _AREA_CACHE[area_id] = area
    return {a: _AREA_CACHE.get(a) or fetched[a] for a in area_ids if a in _AREA_CACHE or a in fetched}


def _build_area(info: Any | None, polygon: Any | None) -> dict[str, Any]:
    area: dict[str, Any] = {}
    try:
        item = info["items"]
        area["lng"], area["lat"] = float(item["long"]), float(item["lat"])
    except (KeyError, TypeError, ValueError):
        area = {}
    try:
        geom = _simplified(polygon)
    except (ShapelyError, KeyError, TypeError, ValueError, AttributeError):
        return area
    area["geometry"] = mapping(geom)
    if "lng" not in area:
        area["lng"], area["lat"] = geom.centroid.x, geom.centroid.y
    return area


def _simplified(feature_collection: dict[str, Any]):
    """Union of the collection's features, simplified. Raises ValueError when the
    result is not a non-empty valid polygon or multipolygon."""
    # Some upstream polygons are invalid and make the union raise a GEOS
    # TopologyException (9 of 277 areas on 2026-09-19); they are repaired first.
    parts = [shape(f["geometry"]) for f in feature_collection["features"]]
    geom = unary_union([p if p.is_valid else make_valid(p) for p in parts])
    if geom.geom_type == "GeometryCollection":
        # make_valid can add lines and points; only the polygonal parts are kept
        geom = unary_union([g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")])
    geom = geom.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
    if geom.is_empty or not geom.is_valid or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"unusable flood area geometry: {geom.geom_type}")
    return geom


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # The API returns ISO strings without an offset, and its reference does not
    # state a time zone. They are treated as UTC; if they are UK local time, the
    # result is one hour late during British Summer Time.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
