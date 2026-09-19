"""Risk summary for a circle on the map. Pure functions; the API layer supplies the data.

The summary reports what the platform holds for the area: the modelled risk of
the H3 cells inside it, recorded crime at the police.uk street points inside it,
and the events inside it. It does not label an area as safe or unsafe.
"""

from __future__ import annotations

import bisect
import math
from collections import Counter
from datetime import datetime
from typing import Any

import h3

from .features import event_feature
from .models import Event
from .scoring import FINE_RES, MIN_EVENT_RISK, distance_m, event_risk

MAX_EVENTS = 25
MAX_STREETS = 8


def cells_within(lng: float, lat: float, radius_m: float, res: int = FINE_RES) -> list[str]:
    """Cells whose centre lies within radius_m of the point, plus the cell that contains it."""
    spacing = h3.average_hexagon_edge_length(res, unit="m") * math.sqrt(3)
    k = math.ceil(radius_m / spacing) + 1
    home = h3.latlng_to_cell(lat, lng, res)
    cells = [home]
    for cell in h3.grid_disk(home, k):
        clat, clng = h3.cell_to_latlng(cell)
        if cell != home and distance_m(lng, lat, clng, clat) <= radius_m:
            cells.append(cell)
    return cells


def percentile(sorted_values: list[float], value: float) -> float:
    """Share of `sorted_values` that are below `value`, in [0, 1]."""
    if not sorted_values:
        return 0.0
    return bisect.bisect_left(sorted_values, value) / len(sorted_values)


def risk_summary(cells: list[str], scores: dict[str, dict[str, float]], all_scores_sorted: list[float]) -> dict[str, Any]:
    """`scores` maps cell -> {score, live, baseline}. Cells without a row count as 0."""
    rows = [scores.get(c, {"score": 0.0, "live": 0.0, "baseline": 0.0}) for c in cells]
    mean = sum(r["score"] for r in rows) / len(rows)
    return {
        "mean_score": round(mean, 4),
        "max_score": round(max(r["score"] for r in rows), 4),
        "mean_live": round(sum(r["live"] for r in rows) / len(rows), 4),
        "mean_baseline": round(sum(r["baseline"] for r in rows) / len(rows), 4),
        # position of this area's mean among all scored London cells
        "london_percentile": round(percentile(all_scores_sorted, mean), 3),
        "cells": len(cells),
    }


def crime_summary(rows: list[list[Any]], month: str | None, lng: float, lat: float, radius_m: float) -> dict[str, Any]:
    """`rows` are crime-point rows [lng, lat, count, weighted, street, top_categories]."""
    total = 0
    weighted = 0.0
    categories: Counter[str] = Counter()
    streets: Counter[str] = Counter()
    for r in rows:
        if distance_m(lng, lat, r[0], r[1]) > radius_m:
            continue
        total += r[2]
        weighted += r[3]
        categories.update(r[5])
        streets[r[4]] += r[2]
    return {
        "month": month,
        "recorded_crimes": total,
        "weighted": round(weighted, 1),
        # Each street point stores its three most frequent categories, so this
        # breakdown covers most but not all of `recorded_crimes`.
        "top_categories": dict(categories.most_common(6)),
        "top_streets": [{"street": s, "recorded_crimes": n} for s, n in streets.most_common(MAX_STREETS)],
    }


def events_within(events: list[Event], lng: float, lat: float, radius_m: float, now: datetime) -> list[dict[str, Any]]:
    """Active events whose area of effect reaches the circle, highest risk first."""
    found = []
    for ev in events:
        if event_risk(ev, now) < MIN_EVENT_RISK:
            continue
        d = distance_m(lng, lat, ev.lng, ev.lat)
        if d <= radius_m + ev.radius_m:
            feature = event_feature(ev, now)
            feature["properties"]["distance_m"] = round(d)
            found.append(feature)
    found.sort(key=lambda f: f["properties"]["risk"], reverse=True)
    return found[:MAX_EVENTS]
