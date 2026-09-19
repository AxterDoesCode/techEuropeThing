"""Metropolitan Police street-level crime records from data.police.uk.

Published monthly, about two months behind, with locations snapped to
anonymised street points. Used as the baseline layer, not as live events.
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

# Relevance of each police.uk category to personal safety on the street
CATEGORY_WEIGHT = {
    "violent-crime": 1.0,
    "robbery": 1.0,
    "possession-of-weapons": 0.9,
    "theft-from-the-person": 0.7,
    "public-order": 0.6,
    "anti-social-behaviour": 0.4,
    "drugs": 0.4,
    "criminal-damage-arson": 0.4,
    "bicycle-theft": 0.3,
    "vehicle-crime": 0.3,
    "burglary": 0.3,
    "other-theft": 0.3,
    "shoplifting": 0.2,
    "other-crime": 0.2,
}

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
    """All crime records in the bbox for one month (default: latest published)."""
    with httpx.Client(timeout=90) as client:
        month = month or latest_month(client)
        with ThreadPoolExecutor(WORKERS) as pool:
            results = pool.map(lambda t: _fetch_tile(client, t, month), _tiles(bbox))
        # A crime on a tile edge can be returned by both tiles
        unique = {c["id"]: c for tile in results for c in tile}
    return month, list(unique.values())


def aggregate_points(crimes: list[dict[str, Any]]) -> list[CrimePoint]:
    """One entry per anonymised street point, with counts by category."""
    points: dict[tuple[str, str], CrimePoint] = {}
    for c in crimes:
        loc = c["location"]
        key = (loc["longitude"], loc["latitude"])
        lng, lat = float(key[0]), float(key[1])
        if not in_london(lng, lat):
            continue
        p = points.get(key)
        if p is None:
            p = points[key] = CrimePoint(lng=lng, lat=lat, street=loc["street"]["name"])
        p.count += 1
        p.weighted += CATEGORY_WEIGHT.get(c["category"], 0.2)
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


def points_payload(points: list[CrimePoint], month: str) -> dict[str, Any]:
    """Compact form for the client: one row per street point,
    [lng, lat, count, weighted, street, {category: count} for the top 3]."""
    return {
        "month": month,
        "columns": ["lng", "lat", "count", "weighted", "street", "top_categories"],
        "rows": [
            [p.lng, p.lat, p.count, round(p.weighted, 1), p.street, dict(p.categories.most_common(3))]
            for p in sorted(points, key=lambda p: p.weighted)
        ],
    }
