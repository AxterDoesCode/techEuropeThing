from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

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
    radius_m: float
    # events only merge with events of the same group
    group: str


CATEGORY_DEFAULTS: dict[Category, CategoryDefaults] = {
    Category.VIOLENT_CRIME: CategoryDefaults(250, "crime"),
    Category.PROPERTY_CRIME: CategoryDefaults(200, "crime"),
    Category.DISORDER: CategoryDefaults(300, "disorder"),
    Category.FIRE: CategoryDefaults(200, "fire"),
    Category.ROAD_CLOSURE: CategoryDefaults(150, "road"),
    Category.TRANSIT_DISRUPTION: CategoryDefaults(200, "transit"),
    Category.FLOOD: CategoryDefaults(100, "flood"),
    Category.WEATHER: CategoryDefaults(2000, "weather"),
    Category.OTHER: CategoryDefaults(200, "other"),
}


class Subtype(str, Enum):
    """Closed list of sub-types an extractor may assign. `Event.subtype` is null
    when the data does not reliably indicate one ("other" is stored as null)."""

    HOMICIDE = "homicide"
    STABBING = "stabbing"
    SHOOTING = "shooting"
    SEXUAL_ASSAULT = "sexual_assault"
    ACID_ATTACK = "acid_attack"
    ROBBERY = "robbery"
    ASSAULT = "assault"
    # marauding or terror attack
    ACTIVE_ATTACK = "active_attack"
    EXPLOSION = "explosion"
    VIOLENT_DISORDER = "violent_disorder"
    TENSE_PROTEST = "tense_protest"
    PEACEFUL_PROTEST = "peaceful_protest"
    FIRE = "fire"
    THEFT = "theft"
    OTHER = "other"


@dataclass(frozen=True)
class Timing:
    """Lifecycle timing of one kind of event, in hours (see scoring.event_end and
    scoring.event_phase).

    freshness_h: an event reported as ongoing by a news report or statement stays
      ongoing while its last confirmation is at most this old; after that it counts
      as ended at `last_confirmed_at`. None: a report cannot make this kind of
      event ongoing. Not used for feed-managed events, which are ongoing while
      their feed lists them.
    half_life_h: half-life of the residual risk after the event ended.
    hard_cap_h: the risk is exactly 0 once the event ended longer ago than this.
      Also the ingestion limit: an incident older than this creates no event.
    freshness_with_end_h: replaces freshness_h when the event has a stated end
      time (`expires_at`).
    """

    freshness_h: float | None
    half_life_h: float
    hard_cap_h: float
    freshness_with_end_h: float | None = None


# REVISIT(lifecycle-timing): first values from the product owner, deliberately not
# tuned. All lifecycle durations are in this table.
# Accepted placeholders:
# - disorder: freshness (12 h) equals the hard cap (12 h), so a protest that is no
#   longer confirmed goes from full risk to 0 in one step, without a residual;
# - violent crime with a null subtype uses the robbery/assault row.
_SERIOUS_VIOLENCE = Timing(freshness_h=6, half_life_h=18, hard_cap_h=72)
_ROBBERY_ASSAULT = Timing(freshness_h=3, half_life_h=8, hard_cap_h=36)
_MAJOR_ATTACK = Timing(freshness_h=2, half_life_h=6, hard_cap_h=48)

# Keyed by (category, subtype). (category, None) is the row for that category when
# the subtype is null or has no row of its own.
TIMING: dict[tuple[Category, str | None], Timing] = {
    (Category.VIOLENT_CRIME, "homicide"): _SERIOUS_VIOLENCE,
    (Category.VIOLENT_CRIME, "stabbing"): _SERIOUS_VIOLENCE,
    (Category.VIOLENT_CRIME, "shooting"): _SERIOUS_VIOLENCE,
    (Category.VIOLENT_CRIME, "sexual_assault"): _SERIOUS_VIOLENCE,
    (Category.VIOLENT_CRIME, "acid_attack"): _SERIOUS_VIOLENCE,
    (Category.VIOLENT_CRIME, "active_attack"): _MAJOR_ATTACK,
    (Category.VIOLENT_CRIME, "explosion"): _MAJOR_ATTACK,
    (Category.FIRE, "explosion"): _MAJOR_ATTACK,
    (Category.OTHER, "explosion"): _MAJOR_ATTACK,
    # robbery, assault, and violent crime of unknown subtype
    (Category.VIOLENT_CRIME, None): _ROBBERY_ASSAULT,
    (Category.PROPERTY_CRIME, None): Timing(freshness_h=None, half_life_h=6, hard_cap_h=24),
    (Category.DISORDER, None): Timing(freshness_h=12, half_life_h=2, hard_cap_h=12, freshness_with_end_h=24),
    (Category.FIRE, None): Timing(freshness_h=6, half_life_h=2, hard_cap_h=12),
    # Floods, road incidents and transit incidents normally come from feeds, which
    # manage their own end. The freshness value applies only to a news report of
    # such an event (not an owner value: 6 h flood, 1 h road and transit incident).
    (Category.FLOOD, None): Timing(freshness_h=6, half_life_h=6, hard_cap_h=24),
    (Category.ROAD_CLOSURE, None): Timing(freshness_h=1, half_life_h=0.25, hard_cap_h=1),
    (Category.TRANSIT_DISRUPTION, None): Timing(freshness_h=1, half_life_h=0.25, hard_cap_h=1),
    # No owner values for these two; short values so that a vague event leaves
    # the map quickly.
    (Category.WEATHER, None): Timing(freshness_h=3, half_life_h=1, hard_cap_h=6),
    (Category.OTHER, None): Timing(freshness_h=2, half_life_h=1, hard_cap_h=6),
}

MAX_HARD_CAP_H = max(t.hard_cap_h for t in TIMING.values())
MAX_FRESHNESS_H = max(max(t.freshness_h or 0, t.freshness_with_end_h or 0) for t in TIMING.values())


def timing_for(category: Category, subtype: str | None = None) -> Timing:
    return TIMING.get((category, subtype)) or TIMING[(category, None)]


SOURCE_TYPE_CONFIDENCE = {
    "official_feed": 0.95,
    "official_statement": 0.9,
    "news": 0.7,
    "manual": 0.5,
    # A single social media post. Such events matter once they merge with other sources.
    "social": 0.35,
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
    # One of the Subtype values, or null when the data does not reliably indicate one
    subtype: str | None = None
    # Start of the incident (not the publication time of a report about it)
    occurred_at: datetime
    # End time stated by the source
    expires_at: datetime | None = None
    # Set when the event left its feed, or when a report said it was over
    ended_at: datetime | None = None
    # A report said the event was in progress. It stays set when the event ends
    # (ended_at, expires_at or the freshness window give the end). A one-off
    # incident has is_ongoing False and counts as ended at occurred_at.
    is_ongoing: bool = False
    # True for events of snapshot feeds. Such an event is in progress until
    # ended_at or expires_at; the freshness window is not applied to it.
    feed_managed: bool = False
    # Time of the last report that confirmed the event. Null is read as occurred_at;
    # storage sets it on insert.
    last_confirmed_at: datetime | None = None
    source_ids: list[str]
    raw_item_ids: list[int] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    # True for events from unstructured sources (news, manual). Such an event may
    # be folded into an existing event of the same category group instead of
    # becoming a new row. Not stored and not serialised; it only directs storage.
    mergeable: bool = Field(default=False, exclude=True)
    # Set by the extraction agent when the item is follow-up reporting on an
    # existing event. Not stored and not serialised; consumed by the storage merge step.
    merge_into: UUID | None = Field(default=None, exclude=True)
    # Set by an extractor when the report says the incident is over: "resolved"
    # (arrest made, scene cleared) or "false_alarm" (false alarm, all-clear). Not
    # stored and not serialised. Storage ends the event the report belongs to; a
    # false_alarm report never creates an event.
    resolution: str | None = Field(default=None, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _legacy_half_life(cls, data: Any) -> Any:
        # Transitional: the structured sources still pass `half_life_min=None`,
        # which meant "no decay while the feed lists the event". It is read as
        # feed_managed. Any other value is dropped. Remove this validator and the
        # `half_life_min` property once the sources pass feed_managed=True.
        if isinstance(data, dict) and "half_life_min" in data:
            data = dict(data)
            if data.pop("half_life_min") is None:
                data.setdefault("feed_managed", True)
                data.setdefault("is_ongoing", True)
        return data

    @property
    def half_life_min(self) -> float | None:
        """Deprecated, see _legacy_half_life. None for a feed-managed event,
        otherwise the residual half-life in minutes."""
        return None if self.feed_managed else self.timing.half_life_h * 60

    @field_validator("occurred_at", "expires_at", "ended_at", "last_confirmed_at")
    @classmethod
    def _require_tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return v

    @property
    def group(self) -> str:
        return CATEGORY_DEFAULTS[self.category].group

    @property
    def timing(self) -> Timing:
        return timing_for(self.category, self.subtype)


class CellScore(BaseModel):
    h3: str
    res: int
    live: float
    baseline: float
    score: float
    top_event_ids: list[UUID] = Field(default_factory=list)
