"""Event construction shared by the extractors of unstructured sources.

Coordinates, radius and confidence are always derived here from a geocode
result and the source type, for the rule-based extractor and the LLM agent alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .geocode import GeoResult
from .models import SOURCE_TYPE_CONFIDENCE, Category, Event, in_london, utcnow
from .scoring import event_phase

# Geocode matches wider than this are area-level (a district, not a street)
STREET_LEVEL_M = 400.0
AREA_CONFIDENCE_FACTOR = 0.6
MIN_RADIUS_M = 250.0
# Largest radius of an event. A district match has a precision of 2 to 3 km; used
# as the radius it spread one incident over a large part of inner London. The
# uncertainty of such a match is expressed by AREA_CONFIDENCE_FACTOR instead.
MAX_RADIUS_M = 800.0


@dataclass(frozen=True)
class Article:
    title: str
    description: str
    url: str | None
    published_at: datetime | None
    source_id: str
    # upstream identifier of the item; part of the event's external_ref
    guid: str


def confidence_for(source_type: str, precision_m: float) -> float:
    confidence = SOURCE_TYPE_CONFIDENCE[source_type]
    if precision_m > STREET_LEVEL_M:
        confidence *= AREA_CONFIDENCE_FACTOR
    return confidence


# REVISIT(severity-scale): modifiers applied by code to the anchor severity that
# an extractor assigns (the anchors are in llm.SYSTEM_PROMPT and extract_rules).
SUSPECT_AT_LARGE_BONUS = 0.05
RESOLVED_FACTOR = 0.6


def adjusted_severity(anchor: float, suspect_at_large: bool, resolved: bool) -> float:
    """Arrest made or scene cleared: x0.6. Otherwise suspect at large: +0.05, at most 1."""
    if resolved:
        return anchor * RESOLVED_FACTOR
    if suspect_at_large:
        return min(1.0, anchor + SUSPECT_AT_LARGE_BONUS)
    return anchor


def radius_for(precision_m: float) -> float:
    return min(MAX_RADIUS_M, max(MIN_RADIUS_M, precision_m))


def located_event(
    *,
    article: Article,
    source_id: str,
    source_type: str,
    external_ref: str,
    category: Category,
    title: str,
    summary: str | None,
    severity: float,
    place: GeoResult,
    occurred_at: datetime | None = None,
    merge_into: UUID | None = None,
    subtype: str | None = None,
    is_ongoing: bool = False,
    expires_at: datetime | None = None,
    resolution: str | None = None,
) -> Event | None:
    """None when the place is outside Greater London, and when the event would
    have no risk left now (ingestion gate): an incident that is over and older
    than the hard cap of its kind, such as a police appeal published long after
    the offence. An event reported as in progress passes whatever its start time.

    `occurred_at` is the time of the incident. When it is None the publication
    time is used; the caller decides beforehand whether that is justified (the
    article indicates a recent incident)."""
    if not in_london(place.lng, place.lat):
        return None
    now = utcnow()
    reported_at = min(article.published_at or now, now)
    occurred_at = occurred_at or reported_at
    confidence = confidence_for(source_type, place.precision_m)
    ev = Event(
        external_ref=external_ref,
        category=category,
        title=title[:200],
        summary=summary or None,
        geometry={"type": "Point", "coordinates": [place.lng, place.lat]},
        lng=place.lng,
        lat=place.lat,
        radius_m=radius_for(place.precision_m),
        severity=severity,
        confidence=confidence,
        source_confidence={source_id: confidence},
        subtype=subtype,
        occurred_at=occurred_at,
        expires_at=expires_at,
        is_ongoing=is_ongoing,
        # the report confirms the event at its publication time
        last_confirmed_at=max(reported_at, occurred_at),
        resolution=resolution,
        source_ids=[source_id],
        urls=[article.url] if article.url else [],
        merge_into=merge_into,
        # Every located event comes from an unstructured source (news, manual)
        mergeable=True,
    )
    # A report that ends an existing event is passed on to storage in any case
    if resolution is None and event_phase(ev, now) == 0.0:
        return None
    return ev
