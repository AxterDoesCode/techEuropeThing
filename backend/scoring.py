"""Risk decay, cell aggregation and event merging. Pure functions, no I/O."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Iterable

import h3
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from .models import ENDED_HALF_LIFE_MIN, CellScore, Event

FINE_RES = 9
COARSE_RES = 7
MIN_EVENT_RISK = 0.05
MIN_CELL_SCORE = 0.01
BASELINE_WEIGHT = 0.4
MERGE_WINDOW = timedelta(hours=3)
TOP_EVENTS_PER_CELL = 5

# Equirectangular projection around central London. Error is under 0.5% across
# the Greater London bbox, which is small against event radii.
_REF_LAT = 51.5
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LNG = _M_PER_DEG_LAT * math.cos(math.radians(_REF_LAT))


def _to_metres(geom: BaseGeometry) -> BaseGeometry:
    return transform(lambda x, y, z=None: (x * _M_PER_DEG_LNG, y * _M_PER_DEG_LAT), geom)


def distance_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    return math.hypot((lng1 - lng2) * _M_PER_DEG_LNG, (lat1 - lat2) * _M_PER_DEG_LAT)


def _half_life_decay(elapsed_min: float, half_life_min: float) -> float:
    return 0.5 ** (max(0.0, elapsed_min) / half_life_min)


def event_risk(ev: Event, t: datetime) -> float:
    """severity * confidence * decay, in [0, 1]. Zero before the event occurred."""
    if t < ev.occurred_at:
        return 0.0
    decay = 1.0
    if ev.half_life_min is not None:
        decay *= _half_life_decay((t - ev.occurred_at).total_seconds() / 60, ev.half_life_min)
    ended_at = ev.ended_at
    if ev.expires_at is not None and (ended_at is None or ev.expires_at < ended_at):
        ended_at = ev.expires_at
    if ended_at is not None and t > ended_at:
        decay *= _half_life_decay((t - ended_at).total_seconds() / 60, ENDED_HALF_LIFE_MIN)
    return ev.severity * ev.confidence * decay


def cell_weights(ev: Event, res: int = FINE_RES) -> dict[str, float]:
    """Spatial weight in (0, 1] for every cell the event reaches.

    Weight is 1 - d / radius, where d is the distance from the event geometry to
    the cell, approximated as distance to the cell centre minus the cell's
    inradius. A point event therefore gives weight 1 to the cell that contains it.
    """
    geom_m = _to_metres(shape(ev.geometry))
    inradius = h3.average_hexagon_edge_length(res, unit="m") * math.sqrt(3) / 2
    centre_spacing = 2 * inradius

    centroid_m = _to_metres(Point(ev.lng, ev.lat))
    extent = geom_m.hausdorff_distance(centroid_m) if not geom_m.is_empty else 0.0
    k = math.ceil((extent + ev.radius_m) / centre_spacing) + 1

    weights: dict[str, float] = {}
    for cell in h3.grid_disk(h3.latlng_to_cell(ev.lat, ev.lng, res), k):
        lat, lng = h3.cell_to_latlng(cell)
        centre = Point(lng * _M_PER_DEG_LNG, lat * _M_PER_DEG_LAT)
        d = max(0.0, geom_m.distance(centre) - inradius)
        w = 1.0 - d / ev.radius_m
        if w > 0:
            weights[cell] = w
    return weights


def combine(values: Iterable[float]) -> float:
    """1 - prod(1 - v). Bounded by 1 and independent of order."""
    remaining = 1.0
    for v in values:
        remaining *= 1.0 - v
    return 1.0 - remaining


def with_baseline(live: float, baseline: float) -> float:
    return 1.0 - (1.0 - live) * (1.0 - BASELINE_WEIGHT * baseline)


def compute_cell_scores(
    events: Iterable[Event],
    t: datetime,
    baseline: dict[str, float] | None = None,
) -> list[CellScore]:
    """Scores at FINE_RES for all cells touched by active events or the baseline,
    plus COARSE_RES scores derived from them."""
    baseline = baseline or {}
    contributions: dict[str, list[tuple[float, Event]]] = {}
    for ev in events:
        risk = event_risk(ev, t)
        if risk < MIN_EVENT_RISK:
            continue
        for cell, w in cell_weights(ev).items():
            contributions.setdefault(cell, []).append((risk * w, ev))

    fine: list[CellScore] = []
    for cell in contributions.keys() | baseline.keys():
        parts = sorted(contributions.get(cell, []), key=lambda p: p[0], reverse=True)
        live = combine(v for v, _ in parts)
        base = baseline.get(cell, 0.0)
        score = with_baseline(live, base)
        if score < MIN_CELL_SCORE:
            continue
        fine.append(
            CellScore(
                h3=cell,
                res=FINE_RES,
                live=live,
                baseline=base,
                score=score,
                top_event_ids=[ev.id for _, ev in parts[:TOP_EVENTS_PER_CELL] if ev.id],
            )
        )
    return fine + _rollup(fine)


def _rollup(fine: list[CellScore]) -> list[CellScore]:
    """Coarse value = mean of (max child, mean over all children). The max term
    keeps a single high-risk street visible at city zoom; the mean term separates
    areas with many affected cells from areas with one."""
    children_per_parent = 7 ** (FINE_RES - COARSE_RES)
    by_parent: dict[str, list[CellScore]] = {}
    for c in fine:
        by_parent.setdefault(h3.cell_to_parent(c.h3, COARSE_RES), []).append(c)

    def agg(values: list[float]) -> float:
        return 0.5 * max(values) + 0.5 * sum(values) / children_per_parent

    out: list[CellScore] = []
    for parent, kids in by_parent.items():
        top = sorted(kids, key=lambda c: c.live, reverse=True)
        ids = [i for c in top for i in c.top_event_ids]
        out.append(
            CellScore(
                h3=parent,
                res=COARSE_RES,
                live=agg([c.live for c in kids]),
                baseline=agg([c.baseline for c in kids]),
                score=agg([c.score for c in kids]),
                top_event_ids=list(dict.fromkeys(ids))[:TOP_EVENTS_PER_CELL],
            )
        )
    return out


def should_merge(a: Event, b: Event) -> bool:
    """True when two events describe the same incident. At least one of them
    must be mergeable (from an unstructured source). Two events from structured
    feeds are matched by external_ref only and never merge here."""
    if a.group != b.group:
        return False
    if not (a.mergeable or b.mergeable):
        return False
    if abs(a.occurred_at - b.occurred_at) > MERGE_WINDOW:
        return False
    return distance_m(a.lng, a.lat, b.lng, b.lat) <= max(a.radius_m, b.radius_m)


def merge(existing: Event, new: Event) -> Event:
    """Fold `new` into `existing`. The smaller radius is treated as the more
    precise location, and its geometry is kept."""
    precise = new if new.radius_m < existing.radius_m else existing
    source_confidence = dict(existing.source_confidence)
    for src, c in new.source_confidence.items():
        source_confidence[src] = max(c, source_confidence.get(src, 0.0))
    ended_at = None
    if existing.ended_at and new.ended_at:
        ended_at = max(existing.ended_at, new.ended_at)
    return existing.model_copy(
        update={
            "external_ref": existing.external_ref or new.external_ref,
            "category": existing.category if existing.severity >= new.severity else new.category,
            "summary": existing.summary or new.summary,
            "geometry": precise.geometry,
            "lng": precise.lng,
            "lat": precise.lat,
            "radius_m": precise.radius_m,
            "severity": max(existing.severity, new.severity),
            "confidence": combine(source_confidence.values()),
            "source_confidence": source_confidence,
            "occurred_at": min(existing.occurred_at, new.occurred_at),
            "ended_at": ended_at,
            "source_ids": list(dict.fromkeys(existing.source_ids + new.source_ids)),
            "raw_item_ids": list(dict.fromkeys(existing.raw_item_ids + new.raw_item_ids)),
            "urls": list(dict.fromkeys(existing.urls + new.urls)),
        }
    )
