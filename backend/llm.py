"""Tool-using LLM extraction agent for unstructured sources (pydantic-ai).

The model reads one article, calls tools to fetch the body, geocode places and
look up existing events, and returns a list of ExtractedEvent. It never outputs
coordinates or confidence: `to_events` derives those from the geocode results
recorded during the same run.

The model is selected with the LLM_MODEL environment variable
(the project uses `google:gemini-3.8-flash`, with `GOOGLE_API_KEY`). The Agent is built on first use, so this
module can be imported without a model or an API key.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from . import geocode as geocode_module
from .geocode import GeoResult
from .located import Article, adjusted_severity, located_event
from .models import Category, Event, Subtype, in_london, utcnow
from .scoring import distance_m

MAX_TOOL_CALLS = 8
# One request per tool-call round plus the final answer can never exceed this
MAX_REQUESTS = 8
RUN_TIMEOUT_S = 90.0
ARTICLE_MAX_CHARS = 6000
MAX_GEOCODE_CANDIDATES = 3
MAX_SIMILAR_EVENTS = 5
# Search radius for follow-up reporting on an existing event
MIN_SIMILAR_RADIUS_M = 500.0

# REVISIT(severity-scale): the severity anchors at the end of this prompt are first
# values from the product owner, not tuned. extract_rules.py uses the same anchors.
SYSTEM_PROMPT = """\
You extract incidents from one news article or police statement for a live map of \
personal-safety risk for people walking in Greater London.

Output a list of incidents. The list is empty when the article describes none. One \
article can describe several separate incidents; output one item per incident.

Include an incident only when all of these hold:
- it is a specific event with its own time and place (not an anniversary, a \
statistic or commentary). Whether it is recent enough is decided by code from the \
time fields below;
- it happened at an identifiable place inside Greater London;
- it affects the safety of someone walking nearby: violence, robbery, sexual offences, \
disorder, protest, fire, explosion, serious collision, flooding, a police cordon or evacuation.

Time of the incident. Police appeals and news follow-ups are often published weeks or \
years after the offence, and the publication time is not the incident time:
- `occurred_at` is the date and time the incident itself happened (for an event in \
progress: when it started), as stated in the article body. Call `fetch_article` when the \
headline and description do not state it. Use 00:00 when only the date is given. Output \
the item even when that date is long ago; code decides whether it is still relevant. \
Null only when the article gives no date or time for the incident. Never copy the \
publication time into it.
- `is_recent`: true only when the text indicates that the incident happened within about \
a day before publication or is in progress ("this morning", "last night", "officers \
remain at the scene"). False when the text gives no such indication. When `occurred_at` \
is null and `is_recent` is false the item is discarded.
- `recency_reason`: the words of the article that justify `is_recent` and `occurred_at`.

Reject: court outcomes, charges, trials, sentencing, inquests, police misconduct and \
dismissals, policy, statistics, recruitment, awards, opinion and commentary.

Location rules:
- Never output coordinates. Always call `geocode` and put a `place_id` it returned in \
this run into the output. An item without such a place_id is discarded.
- Prefer the most specific place named: a street, junction or station rather than a \
district or borough. If `geocode` returns nothing, retry with a different phrasing \
(street plus district, or the station name), then fall back to the district.
- The headline and description often lack the location. Call `fetch_article` with the \
article URL when you need the body text.
- `place_text` is the place wording you geocoded.

Follow-up reporting: before returning an incident, call `find_similar_events` with its \
category, place_id and time. If one of the returned events is the same incident, set \
`existing_event_id` to its event_id. Otherwise leave it null.

You may make at most 8 tool calls per article.

Fields:
- category: violent_crime, property_crime, disorder, fire, road_closure (collisions and \
closures), transit_disruption, flood, weather, other.
- title: at most 120 characters, factual, names the place.
- summary: one or two factual sentences.
- subtype: one of homicide, stabbing, shooting, sexual_assault, acid_attack, robbery, \
assault, active_attack (marauding or terror attack), explosion, violent_disorder, \
tense_protest, peaceful_protest, fire, theft, other. Null unless the article clearly \
states it.
- occurred_at, is_recent, recency_reason: see "Time of the incident".
- is_ongoing: true only when the article says the event is in progress now: a protest \
or disorder still under way, a fire not out, an attack in progress, a cordon or evacuation \
in place. False for a one-off incident that is over (a stabbing, a robbery), also when \
the investigation continues.
- expected_end: ISO 8601 end time when the article states one (a march due to finish at \
17:00), else null.
- suspect_at_large: true when the article says a violent suspect has not been found.
- resolved: true when the article says an arrest was made or the scene has been cleared, \
and no danger remains.
- false_alarm: true when the article says the incident was a false alarm, a hoax, or that \
an all-clear was given. Such an item creates no event; set `existing_event_id` when it \
refers to an event returned by `find_similar_events`, which is then ended.
- severity, from 0 to 1. Use the anchor of the closest row; do not adjust it for \
suspect_at_large or resolved, code does that:
  | Incident | severity |
  | attack in progress, explosion, shooting in progress | 1.0 |
  | homicide, shooting, stabbing with serious injury | 0.9 |
  | sexual assault by a stranger, acid attack | 0.8 |
  | armed robbery, serious assault | 0.7 |
  | violent disorder, riot, large fire with evacuation | 0.6 |
  | robbery without a weapon, collision | 0.5 |
  | tense protest (police lines, scuffles) | 0.4 |
  | large peaceful protest | 0.3 |
  | theft or pickpocketing reports, small contained fire | 0.2 |
  | informational | 0.1 |
"""


class ExtractedEvent(BaseModel):
    category: Category
    title: str = Field(max_length=120)
    summary: str
    # must be a place_id returned by the geocode tool in the same run
    place_id: str
    place_text: str
    severity: float = Field(ge=0, le=1)
    subtype: Subtype | None = None
    # time of the incident from the article body, never the publication time
    occurred_at: datetime | None = None
    # Required, without a default: the model has to decide it for every item. With
    # occurred_at null, the publication time is used only when this is true.
    is_recent: bool
    recency_reason: str = ""
    is_ongoing: bool = False
    expected_end: datetime | None = None
    suspect_at_large: bool = False
    resolved: bool = False
    false_alarm: bool = False
    existing_event_id: str | None = None


class SimilarEventLookup(Protocol):
    def find_merge_candidates(
        self, category: str, lng: float, lat: float, occurred_at: datetime, radius_m: float
    ) -> list[Event]: ...


def fetch_html(url: str) -> str:
    resp = httpx.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


@dataclass
class ExtractionDeps:
    article: Article
    geocoder: Callable[[str], GeoResult | list[GeoResult] | None] = geocode_module.geocode
    # returns the HTML of a page
    fetcher: Callable[[str], str] = fetch_html
    lookup: SimilarEventLookup | None = None
    # geocode results of this run, keyed by place_id
    places: dict[str, GeoResult] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionUsage:
    requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------- tools


_DROPPED_BLOCKS = re.compile(
    r"<(script|style|nav|header|footer|aside|form|noscript|svg)\b.*?</\1\s*>", re.I | re.S
)
_MAIN_BLOCK = re.compile(r"<(article|main)\b.*?</\1\s*>", re.I | re.S)


def html_to_text(page: str, max_chars: int = ARTICLE_MAX_CHARS) -> str:
    page = re.sub(r"<!--.*?-->", " ", page, flags=re.S)
    page = _DROPPED_BLOCKS.sub(" ", page)
    main = _MAIN_BLOCK.search(page)
    if main:
        page = main.group(0)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    return " ".join(text.split())[:max_chars]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def fetch_article(ctx: RunContext[ExtractionDeps], url: str) -> str:
    """Fetch the article body as plain text (truncated). Only the article's own URL,
    or another page on the same host, can be fetched.

    Args:
        url: The article URL given in the prompt.
    """
    own = ctx.deps.article.url
    if not own:
        return "error: this item has no URL; all of its text is in the prompt"
    if urlparse(url).scheme not in ("http", "https") or _host(url) != _host(own):
        return f"error: only pages on {_host(own)} can be fetched"
    try:
        return html_to_text(ctx.deps.fetcher(url)) or "error: the page has no text"
    except Exception as exc:
        return f"error: fetch failed ({type(exc).__name__})"


def place_id_for(place_text: str, index: int = 0) -> str:
    key = " ".join(place_text.lower().split())
    digest = hashlib.sha1(key.encode()).hexdigest()[:8]
    return f"p_{digest}" if index == 0 else f"p_{digest}_{index}"


def geocode(ctx: RunContext[ExtractionDeps], place_text: str) -> list[dict[str, Any]]:
    """Resolve a place name in Greater London. Returns up to 3 candidates, best first.
    An empty list means the place was not found in London: retry with another phrasing.
    `precision_m` is the approximate radius of the match; below 400 is street level.

    Args:
        place_text: Street, junction, station, postcode or district, e.g. "Lloyd Baker Street, Clerkenwell".
    """
    try:
        found = ctx.deps.geocoder(place_text)
    except Exception:
        return []
    results = found if isinstance(found, list) else [found] if found else []
    candidates = []
    for r in [r for r in results if in_london(r.lng, r.lat)][:MAX_GEOCODE_CANDIDATES]:
        place_id = place_id_for(place_text, len(candidates))
        ctx.deps.places[place_id] = r
        candidates.append(
            {
                "place_id": place_id,
                "label": r.label,
                "precision_m": round(r.precision_m),
                "lat": round(r.lat, 5),
                "lng": round(r.lng, 5),
            }
        )
    return candidates


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    try:
        return _aware(datetime.fromisoformat((value or "").replace("Z", "+00:00")))
    except ValueError:
        return None


def find_similar_events(
    ctx: RunContext[ExtractionDeps], category: str, place_id: str, occurred_at_iso: str | None = None
) -> list[dict[str, Any]]:
    """List existing events of the same kind near a place and time. Use it to detect
    follow-up reporting on an incident that is already on the map.

    Args:
        category: Category of the incident being extracted.
        place_id: A place_id returned by `geocode` in this run.
        occurred_at_iso: ISO 8601 time of the incident; omit to use the publication time.
    """
    place = ctx.deps.places.get(place_id)
    if ctx.deps.lookup is None or place is None:
        return []
    occurred_at = _parse_iso(occurred_at_iso) or ctx.deps.article.published_at or utcnow()
    radius_m = max(MIN_SIMILAR_RADIUS_M, place.precision_m)
    try:
        events = ctx.deps.lookup.find_merge_candidates(category, place.lng, place.lat, occurred_at, radius_m)
    except Exception:
        # includes a storage backend that does not implement the lookup yet
        return []
    return [
        {
            "event_id": str(ev.id),
            "title": ev.title,
            "category": ev.category.value,
            "occurred_at": ev.occurred_at.isoformat(),
            "distance_m": round(distance_m(place.lng, place.lat, ev.lng, ev.lat)),
        }
        for ev in events[:MAX_SIMILAR_EVENTS]
    ]


# ---------------------------------------------------------------- agent


def get_model_name() -> str:
    return os.environ.get("LLM_MODEL", "").strip()


def is_configured() -> bool:
    return bool(os.environ.get("LLM_MODEL", "").strip())


def get_model() -> Model | str:
    return os.environ["LLM_MODEL"].strip()


_agent: Agent[ExtractionDeps, list[ExtractedEvent]] | None = None


def get_agent() -> Agent[ExtractionDeps, list[ExtractedEvent]]:
    """The agent is built without a model; `run` passes the model per call."""
    global _agent
    if _agent is None:
        _agent = Agent(
            None,
            output_type=list[ExtractedEvent],
            deps_type=ExtractionDeps,
            system_prompt=SYSTEM_PROMPT,
            tools=[fetch_article, geocode, find_similar_events],
            retries=2,
            tool_timeout=30,
            name="extraction",
        )
    return _agent


def article_prompt(article: Article) -> str:
    published = article.published_at.isoformat() if article.published_at else "unknown"
    return (
        f"Source: {article.source_id}\n"
        f"Published: {published}\n"
        f"URL: {article.url or 'none'}\n"
        f"Headline: {article.title}\n"
        f"Text: {article.description or '(none)'}"
    )


def run(deps: ExtractionDeps, model: Model | str | None = None) -> tuple[list[ExtractedEvent], ExtractionUsage]:
    """Run the agent on deps.article. Raises on a model error, an exceeded usage
    limit or a timeout; use `run_counted` when the usage of a failed run is needed."""
    usage = RunUsage()
    return _run(deps, model, usage), _usage_numbers(usage)


def _usage_numbers(usage: RunUsage) -> ExtractionUsage:
    return ExtractionUsage(usage.requests, usage.tool_calls, usage.input_tokens, usage.output_tokens)


def _run(deps: ExtractionDeps, model: Model | str | None, usage: RunUsage) -> list[ExtractedEvent]:
    async def call() -> list[ExtractedEvent]:
        result = await asyncio.wait_for(
            get_agent().run(
                article_prompt(deps.article),
                deps=deps,
                model=model or get_model(),
                usage=usage,
                usage_limits=UsageLimits(request_limit=MAX_REQUESTS, tool_calls_limit=MAX_TOOL_CALLS),
            ),
            RUN_TIMEOUT_S,
        )
        return result.output

    return asyncio.run(call())


def run_counted(
    deps: ExtractionDeps, model: Model | str | None = None
) -> tuple[list[ExtractedEvent] | None, ExtractionUsage, Exception | None]:
    """Like `run`, but a failure is returned instead of raised, together with the
    usage accumulated up to the failure."""
    usage = RunUsage()
    try:
        return _run(deps, model, usage), _usage_numbers(usage), None
    except Exception as exc:
        return None, _usage_numbers(usage), exc


# ---------------------------------------------------------------- guardrails


def _event_uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def to_events(
    extracted: list[ExtractedEvent], deps: ExtractionDeps, source_id: str, source_type: str
) -> list[Event]:
    """Apply the code-side guardrails and build Events. Items whose place_id was
    not produced by the geocode tool in this run, or whose place is outside
    Greater London, are dropped. So are items without an incident time that the
    model did not mark as recent, items older than the hard cap of their kind
    (located_event), and false alarms that refer to no existing event."""
    now = utcnow()
    events: list[Event] = []
    for item in extracted:
        place = deps.places.get(item.place_id)
        if place is None:
            continue
        occurred_at = _aware(item.occurred_at) if item.occurred_at else None
        if occurred_at is not None and occurred_at > now:
            occurred_at = None
        if occurred_at is None and not item.is_recent:
            continue
        merge_into = _event_uuid(item.existing_event_id)
        if item.false_alarm and merge_into is None:
            continue
        resolved = item.resolved or item.false_alarm
        subtype = item.subtype.value if item.subtype not in (None, Subtype.OTHER) else None
        ref = f"{source_id}:{deps.article.guid}"
        ev = located_event(
            article=deps.article,
            source_id=source_id,
            source_type=source_type,
            external_ref=ref if not events else f"{ref}#{len(events) + 1}",
            category=item.category,
            title=item.title,
            summary=item.summary,
            severity=adjusted_severity(item.severity, item.suspect_at_large, resolved),
            place=place,
            occurred_at=occurred_at,
            merge_into=merge_into,
            subtype=subtype,
            is_ongoing=item.is_ongoing and not resolved,
            expires_at=_aware(item.expected_end) if item.expected_end else None,
            resolution="false_alarm" if item.false_alarm else "resolved" if item.resolved else None,
        )
        if ev is not None:
            events.append(ev)
    return events
