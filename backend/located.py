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

# Articles are published hours after the incident, so a longer half-life than
# the category default is used; otherwise every event would arrive already decayed.
HALF_LIFE_MIN = 24 * 60
# Geocode matches wider than this are area-level (a district, not a street)
STREET_LEVEL_M = 400.0
AREA_CONFIDENCE_FACTOR = 0.6
MIN_RADIUS_M = 250.0


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
) -> Event | None:
    """None when the place is outside Greater London."""
    if not in_london(place.lng, place.lat):
        return None
    confidence = confidence_for(source_type, place.precision_m)
    return Event(
        external_ref=external_ref,
        category=category,
        title=title[:200],
        summary=summary or None,
        geometry={"type": "Point", "coordinates": [place.lng, place.lat]},
        lng=place.lng,
        lat=place.lat,
        radius_m=max(MIN_RADIUS_M, place.precision_m),
        severity=severity,
        confidence=confidence,
        source_confidence={source_id: confidence},
        half_life_min=HALF_LIFE_MIN,
        occurred_at=occurred_at or article.published_at or utcnow(),
        source_ids=[source_id],
        urls=[article.url] if article.url else [],
        merge_into=merge_into,
        # Every located event comes from an unstructured source (news, manual)
        mergeable=True,
    )
