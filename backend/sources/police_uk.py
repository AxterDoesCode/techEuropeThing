"""Street-level crime records from data.police.uk.

Published monthly, about two months behind, with locations snapped to
anonymised street points. One month of these records is too noisy to be the
baseline by itself (month-to-month Spearman 0.21 per point). The baseline level
of an area comes from 12 months of MPS LSOA counts (mps_lsoa.py); the records
here only place that level on street points inside each LSOA.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import h3
import httpx

from ..models import LONDON_BBOX, in_london

BASE = "https://data.police.uk/api"
# The API refuses areas holding more than 10,000 crimes with a 503; such tiles are split.
TILE_LNG = 0.06
TILE_LAT = 0.04
MIN_TILE_DEG = 0.004
WORKERS = 6

# police.uk categories that describe a risk to a person walking on the street.
# The others (shoplifting, burglary, vehicle crime, bicycle theft, other theft,
# other crime, drugs, criminal damage, anti-social behaviour) are not counted.
RELEVANT_CATEGORIES = frozenset({
    "violent-crime",
    "robbery",
    "theft-from-the-person",
    "possession-of-weapons",
    "public-order",
})

# police.uk snaps a record to the nearest point of a fixed list, and part of that
# list is venues where crimes are recorded rather than committed (a hospital, a
# custody suite) or that are not the street. A point is dropped when its street
# label contains one of these strings (case-insensitive).
VENUE_LABELS = (
    "hospital",
    "police station",
    "prison",
    "supermarket",
    "shopping area",
    "petrol station",
    "further/higher educational building",
    "airport",
)

Bbox = tuple[float, float, float, float]  # west, south, east, north


@dataclass
class CrimePoint:
    lng: float
    lat: float
    street: str
    count: int = 0
    weighted: float = 0.0
    categories: Counter[str] = field(default_factory=Counter)


def latest_month(client: httpx.Client) -> str:
    resp = client.get(f"{BASE}/crimes-street-dates")
    resp.raise_for_status()
    return resp.json()[0]["date"]


def _tiles(bbox: Bbox) -> list[Bbox]:
    w, s, e, n = bbox
    cols = math.ceil((e - w) / TILE_LNG)
    rows = math.ceil((n - s) / TILE_LAT)
    return [
        (w + c * TILE_LNG, s + r * TILE_LAT, min(e, w + (c + 1) * TILE_LNG), min(n, s + (r + 1) * TILE_LAT))
        for c in range(cols)
        for r in range(rows)
    ]


def _fetch_tile(client: httpx.Client, tile: Bbox, month: str) -> list[dict[str, Any]]:
    w, s, e, n = tile
    poly = f"{s},{w}:{s},{e}:{n},{e}:{n},{w}"
    resp = None
    for attempt in range(4):
        resp = client.get(f"{BASE}/crimes-street/all-crime", params={"poly": poly, "date": month})
        if resp.status_code != 429:  # 429 = over the 15 requests/s limit
            break
        time.sleep(2**attempt)
    assert resp is not None
    if resp.status_code == 503 and (e - w) > MIN_TILE_DEG:
        mx, my = (w + e) / 2, (s + n) / 2
        quarters = [(w, s, mx, my), (mx, s, e, my), (w, my, mx, n), (mx, my, e, n)]
        return [c for q in quarters for c in _fetch_tile(client, q, month)]
    resp.raise_for_status()
    return resp.json()


def fetch_month(month: str | None = None, bbox: Bbox = LONDON_BBOX) -> tuple[str, list[dict[str, Any]]]:
    """All crime records in the bbox for one month (default: latest published).

    The per-category endpoints (/crimes-street/{category}) would return less data,
    but five categories need five times the requests (825 instead of about 180)
    and their small responses arrive fast enough to exceed the 15 requests/s limit
    (measured: 429 responses persisting through the retries). One all-crime pass
    is 42 MB in about 30 s; aggregate_points filters the categories locally."""
    with httpx.Client(timeout=90) as client:
        month = month or latest_month(client)
        with ThreadPoolExecutor(WORKERS) as pool:
            results = pool.map(lambda t: _fetch_tile(client, t, month), _tiles(bbox))
        # A crime on a tile edge can be returned by both tiles
        unique = {c["id"]: c for tile in results for c in tile}
    return month, list(unique.values())


def is_venue(street: str) -> bool:
    label = street.lower()
    return any(v in label for v in VENUE_LABELS)


def aggregate_points(crimes: list[dict[str, Any]]) -> list[CrimePoint]:
    """One entry per anonymised street point, with counts by category. Only
    RELEVANT_CATEGORIES are counted and venue points (VENUE_LABELS) are dropped.
    `weighted` is left at 0; mps_lsoa.distribute sets it."""
    points: dict[tuple[str, str], CrimePoint] = {}
    for c in crimes:
        loc = c["location"]
        if c["category"] not in RELEVANT_CATEGORIES or is_venue(loc["street"]["name"]):
            continue
        key = (loc["longitude"], loc["latitude"])
        lng, lat = float(key[0]), float(key[1])
        if not in_london(lng, lat):
            continue
        p = points.get(key)
        if p is None:
            p = points[key] = CrimePoint(lng=lng, lat=lat, street=loc["street"]["name"])
        p.count += 1
        p.categories[c["category"]] += 1
    return list(points.values())


def baseline_cells(points: list[CrimePoint], months: int, res: int = 9) -> dict[str, float]:
    """Weighted crimes per cell per month, log-scaled and clipped at the 99th
    percentile, normalised to [0, 1]."""
    per_cell: dict[str, float] = defaultdict(float)
    for p in points:
        per_cell[h3.latlng_to_cell(p.lat, p.lng, res)] += p.weighted / months
    if not per_cell:
        return {}
    logs = {cell: math.log1p(v) for cell, v in per_cell.items()}
    ordered = sorted(logs.values())
    cap = ordered[min(len(ordered) - 1, int(0.99 * len(ordered)))] or 1.0
    return {cell: min(1.0, v / cap) for cell, v in logs.items()}


def points_payload(points: list[CrimePoint], month: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact form for the client: one row per street point,
    [lng, lat, count, weighted, street, {category: count} for the top 3].

    `month` is the police.uk month ("YYYY-MM", parsed by the client). `extra` adds
    top-level fields (period, method) and cannot replace month, columns or rows."""
    return {
        **(extra or {}),
        "month": month,
        "columns": ["lng", "lat", "count", "weighted", "street", "top_categories"],
        "rows": [
            [p.lng, p.lat, p.count, round(p.weighted, 3), p.street, dict(p.categories.most_common(3))]
            for p in sorted(points, key=lambda p: p.weighted)
        ],
    }
