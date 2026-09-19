from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Greater London bounding box: west, south, east, north
LONDON_BBOX = (-0.5104, 51.2868, 0.3340, 51.6919)


def in_london(lng: float, lat: float) -> bool:
    w, s, e, n = LONDON_BBOX
    return w <= lng <= e and s <= lat <= n


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Category(str, Enum):
    VIOLENT_CRIME = "violent_crime"
    PROPERTY_CRIME = "property_crime"
    DISORDER = "disorder"
    FIRE = "fire"
    ROAD_CLOSURE = "road_closure"
    TRANSIT_DISRUPTION = "transit_disruption"
    FLOOD = "flood"
    WEATHER = "weather"
    OTHER = "other"


@dataclass(frozen=True)
class CategoryDefaults:
    # None = no decay while the upstream source still lists the event
    half_life_min: float | None
    radius_m: float
    # events only merge with events of the same group
    group: str


CATEGORY_DEFAULTS: dict[Category, CategoryDefaults] = {
    Category.VIOLENT_CRIME: CategoryDefaults(180, 250, "crime"),
    Category.PROPERTY_CRIME: CategoryDefaults(120, 200, "crime"),
    Category.DISORDER: CategoryDefaults(90, 300, "disorder"),
    Category.FIRE: CategoryDefaults(120, 200, "fire"),
    Category.ROAD_CLOSURE: CategoryDefaults(None, 150, "road"),
    Category.TRANSIT_DISRUPTION: CategoryDefaults(None, 200, "transit"),
    Category.FLOOD: CategoryDefaults(None, 100, "flood"),
    Category.WEATHER: CategoryDefaults(60, 2000, "weather"),
    Category.OTHER: CategoryDefaults(60, 200, "other"),
}

# Half-life applied from `ended_at` once an event has left its upstream feed
ENDED_HALF_LIFE_MIN = 30.0

SOURCE_TYPE_CONFIDENCE = {
    "official_feed": 0.95,
    "official_statement": 0.9,
    "news": 0.7,
    "manual": 0.5,
}


class RawItem(BaseModel):
    source_id: str
    external_id: str
    payload: dict[str, Any]


class Event(BaseModel):
    id: UUID | None = None
    external_ref: str | None = None
    category: Category
    title: str = Field(max_length=200)
    summary: str | None = None
    # GeoJSON geometry, coordinates in [lng, lat] order
    geometry: dict[str, Any]
    lng: float
    lat: float
    radius_m: float = Field(gt=0)
    severity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    source_confidence: dict[str, float] = Field(default_factory=dict)
    half_life_min: float | None = None
    occurred_at: datetime
    expires_at: datetime | None = None
    ended_at: datetime | None = None
    source_ids: list[str]
    raw_item_ids: list[int] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)

    @field_validator("occurred_at", "expires_at", "ended_at")
    @classmethod
    def _require_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v

    @property
    def group(self) -> str:
        return CATEGORY_DEFAULTS[self.category].group


class CellScore(BaseModel):
    h3: str
    res: int
    live: float
    baseline: float
    score: float
    top_event_ids: list[UUID] = Field(default_factory=list)
