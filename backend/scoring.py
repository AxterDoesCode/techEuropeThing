"""Risk decay, cell aggregation and event merging. Pure functions, no I/O."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Iterable

import h3
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

from .models import CellScore, Event

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


def event_end(ev: Event, t: datetime) -> datetime | None:
    """The time at which the event ended, as known at `t`; None while it is in
    progress at `t`. The earliest of:

    - `ended_at` (the feed dropped the event, or a report said it was over);
    - `expires_at`, the end time stated by the source;
    - `occurred_at`, when no report says the event is in progress (a one-off
      incident is over when it is reported);
    - `last_confirmed_at`, when a report said the event was in progress but no
      report has confirmed it within the freshness window of its kind.

    The last two do not apply to a feed-managed event.
    """
    ends = [d for d in (ev.ended_at, ev.expires_at) if d is not None]
    if not ev.feed_managed:
        timing = ev.timing
        freshness_h = timing.freshness_h
        if ev.expires_at is not None and timing.freshness_with_end_h is not None:
            freshness_h = timing.freshness_with_end_h
        confirmed = max(ev.last_confirmed_at or ev.occurred_at, ev.occurred_at)
        if not ev.is_ongoing or freshness_h is None:
            ends.append(ev.occurred_at)
        elif t - confirmed > timedelta(hours=freshness_h):
            ends.append(confirmed)
    if not ends:
        return None
    end = max(min(ends), ev.occurred_at)
    return end if end <= t else None


def event_state(ev: Event, t: datetime) -> str:
    """"upcoming" before occurred_at, then "ongoing" until event_end, then "ended"."""
    if t < ev.occurred_at:
        return "upcoming"
    return "ongoing" if event_end(ev, t) is None else "ended"


def event_phase(ev: Event, t: datetime) -> float:
    """0 before occurred_at; 1 while ongoing, however long ago the event started;
    after the end 0.5 ** (time since end / residual half-life), and exactly 0 once
    the time since the end exceeds the hard cap."""
    if t < ev.occurred_at:
        return 0.0
    end = event_end(ev, t)
    if end is None:
        return 1.0
    since_end_h = (t - end).total_seconds() / 3600
    timing = ev.timing
    if since_end_h > timing.hard_cap_h:
        return 0.0
    return 0.5 ** (since_end_h / timing.half_life_h)


def event_risk(ev: Event, t: datetime) -> float:
    """severity * confidence * event_phase, in [0, 1]."""
    return ev.severity * ev.confidence * event_phase(ev, t)


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


def same_time(a: Event, b: Event) -> bool:
    """occurred_at within MERGE_WINDOW, or one event started earlier and was in
    progress when the other occurred (a new report about a protest that started
    two days ago)."""
    if abs(a.occurred_at - b.occurred_at) <= MERGE_WINDOW:
        return True
    first, second = (a, b) if a.occurred_at <= b.occurred_at else (b, a)
    return event_state(first, second.occurred_at) == "ongoing"


def should_merge(a: Event, b: Event) -> bool:
    """True when two events describe the same incident. At least one of them
    must be mergeable (from an unstructured source). Two events from structured
    feeds are matched by external_ref only and never merge here."""
    if a.group != b.group:
        return False
    if not (a.mergeable or b.mergeable):
        return False
    if not same_time(a, b):
        return False
    return distance_m(a.lng, a.lat, b.lng, b.lat) <= max(a.radius_m, b.radius_m)


def merge(existing: Event, new: Event) -> Event:
    """Fold `new` into `existing`. The smaller radius is treated as the more
    precise location, and its geometry is kept. The merge confirms the event
    (last_confirmed_at). A report with a `resolution` ends an event that is not
    feed-managed; a false alarm also sets its severity to 0."""
    precise = new if new.radius_m < existing.radius_m else existing
    source_confidence = dict(existing.source_confidence)
    for src, c in new.source_confidence.items():
        source_confidence[src] = max(c, source_confidence.get(src, 0.0))
    # A merged report confirms the event at the time of that report
    new_confirmed = new.last_confirmed_at or new.occurred_at
    ended_at = None
    if existing.ended_at and new.ended_at:
        ended_at = max(existing.ended_at, new.ended_at)
    elif existing.ended_at and not (new.is_ongoing and new_confirmed > existing.ended_at):
        # only a later report that says the event is in progress reopens it
        ended_at = existing.ended_at
    is_ongoing = existing.is_ongoing or new.is_ongoing
    severity = max(existing.severity, new.severity)
    if new.resolution is not None and not existing.feed_managed:
        # is_ongoing is kept: without it the end would move back to occurred_at
        ended_at = min(existing.ended_at or new_confirmed, new_confirmed)
        if new.resolution == "false_alarm":
            severity = 0.0
    severe = existing if existing.severity >= new.severity else new
    other = new if severe is existing else existing
    subtype = severe.subtype or (other.subtype if other.category == severe.category else None)
    expires_at = existing.expires_at if existing.feed_managed else new.expires_at or existing.expires_at
    return existing.model_copy(
        update={
            "external_ref": existing.external_ref or new.external_ref,
            "category": severe.category,
            "subtype": subtype,
            "summary": existing.summary or new.summary,
            "geometry": precise.geometry,
            "lng": precise.lng,
            "lat": precise.lat,
            "radius_m": precise.radius_m,
            "severity": severity,
            "confidence": combine(source_confidence.values()),
            "source_confidence": source_confidence,
            "occurred_at": min(existing.occurred_at, new.occurred_at),
            "expires_at": expires_at,
            "ended_at": ended_at,
            "is_ongoing": is_ongoing,
            "last_confirmed_at": max(existing.last_confirmed_at or existing.occurred_at, new_confirmed),
            "source_ids": list(dict.fromkeys(existing.source_ids + new.source_ids)),
            "raw_item_ids": list(dict.fromkeys(existing.raw_item_ids + new.raw_item_ids)),
            "urls": list(dict.fromkeys(existing.urls + new.urls)),
        }
    )
