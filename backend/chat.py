"""Question answering over the platform's data. A tool-using LLM agent whose
tools call the public platform API, so its answers use the same data and the
same response shapes as the client applications.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from . import llm
from .geocode import GeoResult, geocode

DEFAULT_API_BASE = "https://alexchau256--london-risk-store-api.modal.run"
MAX_HISTORY_TURNS = 8
LIMITS = UsageLimits(request_limit=10, tool_calls_limit=10)

SYSTEM_PROMPT = """\
You answer questions about personal safety for people on foot in Greater London, using \
only the data returned by your tools. The data: a modelled risk score per map cell \
(0 to 1, combining current events with a baseline of police-recorded crime), recorded \
crime per street point (the tool result states the period covered and the counting \
method in `crime.period` and `crime.method`), current events (police statements, news \
reports, transport and road incidents, flood warnings) with their sources, walking \
routes that compare the shortest path with a lower-risk path, and hotels from OpenStreetMap.

Rules:
- Call `find_place` to turn a place name into coordinates before any other tool. If it \
returns nothing, ask the user for a more specific place.
- Every figure in your answer must come from a tool result of this conversation. State \
the period of the crime data (`crime.period`, else `crime.month`) and the time of the newest event you mention. When events \
have source links, name the sources.
- Report what the data shows: recorded crime counts and categories, current events, the \
score and how it compares with the rest of London (`london_percentile` is the share of \
London cells with a lower score). Do not call a place "safe", "unsafe" or "dangerous" and \
do not describe the people who live there; recorded crime is higher where many people \
gather (stations, nightlife, shopping streets), so say that when it applies.
- The crime data has no time of day. Say so when asked about night-time risk; you can \
still report current events and compare routes.
- For a route question call `walking_route` and compare both routes: distance, minutes, \
mean risk, and the difference between them.
- Outside Greater London, or for topics the tools do not cover, say that this service \
does not have that data. For an emergency tell the user to call 999.
- Be brief: a short paragraph or a few bullet points. No headings.
"""


@dataclass
class ChatDeps:
    api_base: str
    http: httpx.Client
    geocoder: Callable[[str], GeoResult | None] = geocode
    # filled by the tools, returned to the client with the answer
    sources: list[dict[str, str]] = field(default_factory=list)
    places: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)


def _get(ctx: RunContext[ChatDeps], path: str, **params: Any) -> dict[str, Any]:
    resp = ctx.deps.http.get(f"{ctx.deps.api_base}{path}", params=params)
    if resp.status_code >= 400:
        return {"error": resp.json().get("detail", resp.text)[:300]}
    return resp.json()


def find_place(ctx: RunContext[ChatDeps], query: str) -> dict[str, Any]:
    """Coordinates of a place in Greater London: a street, station, landmark, postcode or district."""
    ctx.deps.tool_calls.append(f"find_place({query!r})")
    place = ctx.deps.geocoder(query)
    if place is None:
        return {"found": False}
    found = {"found": True, "label": place.label, "lat": place.lat, "lng": place.lng, "precision_m": place.precision_m}
    ctx.deps.places.append({"query": query, **found})
    return found


def area_report(ctx: RunContext[ChatDeps], lat: float, lng: float, radius_m: int = 500) -> dict[str, Any]:
    """Modelled risk, recorded crime and current events within radius_m (50-3000) of a point."""
    ctx.deps.tool_calls.append(f"area_report({lat:.4f}, {lng:.4f}, {radius_m})")
    data = _get(ctx, "/api/area", lat=lat, lng=lng, radius_m=radius_m)
    events = []
    for f in data.get("events", []):
        p = f["properties"]
        events.append(
            {k: p.get(k) for k in ("title", "category", "risk", "severity", "occurred_at", "source_ids", "distance_m", "urls")}
        )
        for url in p.get("urls", [])[:2]:
            ctx.deps.sources.append({"title": p["title"], "url": url})
    return data | {"events": events}


def walking_route(
    ctx: RunContext[ChatDeps], from_lat: float, from_lng: float, to_lat: float, to_lng: float
) -> dict[str, Any]:
    """Compare the shortest walking route with the lower-risk route between two points."""
    ctx.deps.tool_calls.append(f"walking_route(({from_lat:.4f}, {from_lng:.4f}) -> ({to_lat:.4f}, {to_lng:.4f}))")
    resp = ctx.deps.http.post(
        f"{ctx.deps.api_base}/api/route",
        json={"origin": [from_lng, from_lat], "destination": [to_lng, to_lat]},
    )
    if resp.status_code >= 400:
        return {"error": str(resp.json().get("detail", resp.text))[:300]}
    data = resp.json()
    # geometry and per-step detail are left out: the model needs the figures only
    out = {k: v for k, v in data.items() if k not in ("fast", "safe")}
    for name in ("fast", "safe"):
        out[name] = {k: v for k, v in data[name].items() if k not in ("geometry", "steps")}
    return out


def hotels_near(ctx: RunContext[ChatDeps], lat: float, lng: float, radius_m: int = 1500) -> dict[str, Any]:
    """Hotels within radius_m of a point, lowest modelled risk of their surroundings first."""
    ctx.deps.tool_calls.append(f"hotels_near({lat:.4f}, {lng:.4f}, {radius_m})")
    data = _get(ctx, "/api/hotels", lat=lat, lng=lng, radius_m=radius_m)
    data["hotels"] = [
        {
            "name": h["name"],
            "type": h["details"].get("subtype"),
            "distance_m": h["distance_m"],
            "area_mean_score": h["risk"]["mean_score"],
            "london_percentile": h["risk"]["london_percentile"],
            "nearest_station": h["nearest_station"] and h["nearest_station"]["name"],
            "station_distance_m": h["nearest_station"] and h["nearest_station"]["distance_m"],
        }
        for h in data.get("hotels", [])[:8]
    ]
    return data


_agent: Agent[ChatDeps, str] | None = None


def get_agent() -> Agent[ChatDeps, str]:
    global _agent
    if _agent is None:
        _agent = Agent(
            None,
            deps_type=ChatDeps,
            output_type=str,
            system_prompt=SYSTEM_PROMPT,
            tools=[find_place, area_report, walking_route, hotels_near],
        )
    return _agent


def build_prompt(messages: list[dict[str, str]]) -> str:
    """The last turns as one prompt. The server keeps no conversation state."""
    turns = messages[-MAX_HISTORY_TURNS:]
    if len(turns) == 1:
        return turns[0]["content"]
    lines = [f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in turns[:-1]]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nAnswer the user's new message:\n{turns[-1]['content']}"


def answer(messages: list[dict[str, str]], deps: ChatDeps | None = None, model: Any = None) -> dict[str, Any]:
    own_client = deps is None
    if deps is None:
        deps = ChatDeps(api_base=os.environ.get("PLATFORM_API_BASE", DEFAULT_API_BASE), http=httpx.Client(timeout=30))
    try:
        result = get_agent().run_sync(
            build_prompt(messages), deps=deps, model=model or llm.get_model(), usage_limits=LIMITS
        )
    finally:
        if own_client:
            deps.http.close()
    seen: set[str] = set()
    sources = [s for s in deps.sources if not (s["url"] in seen or seen.add(s["url"]))]
    return {"answer": result.output, "sources": sources[:8], "places": deps.places, "tool_calls": deps.tool_calls}
