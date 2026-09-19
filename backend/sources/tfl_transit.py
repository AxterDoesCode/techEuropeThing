"""TfL rail incidents: emergencies at a station, from two TfL feeds.

Product rule (owner): "we don't care about station closures or anything. We only
care about emergencies or incidents, for example someone dying on the tube which
could cause police and ambulances appearing." An item is kept only when its text
names an incident cause listed in INCIDENT_CAUSES. Planned closures, engineering
works, part closures, exit-only crowd control, lift, escalator and step-free
access notices and information notices produce no event, whatever their
upstream `type`.

Feeds:
- `StopPoint/Mode/{modes}/Disruption`: station-level items. Each names one stop
  (atcoCode). The feed has no coordinates; fetch() resolves the atcoCode of each
  incident item through `StopPoint/{ids}`.
- `Line/Mode/{modes}/Status`: line-level items. An incident reason usually names
  a station ("... due to a customer incident at Stratford"). The name is looked
  up in the stop list of that line (`Line/{id}/StopPoints`) and must equal one
  station name after normalisation. A reason without a resolvable station
  produces no item: a line has no usable point geometry and no location is guessed.

fetch() writes `lat`/`lon` into the payload, so to_event() reads only the
payload. Resolved coordinates and stop lists are cached for the process lifetime.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from datetime import datetime
from typing import Any, Iterator

import httpx

from ..models import (
    CATEGORY_DEFAULTS,
    SOURCE_TYPE_CONFIDENCE,
    Category,
    Event,
    RawItem,
    in_london,
    utcnow,
)
from .base import external_ref

MODES = "tube,overground,dlr,elizabeth-line"
URL = f"https://api.tfl.gov.uk/StopPoint/Mode/{MODES}/Disruption"
LINE_STATUS_URL = f"https://api.tfl.gov.uk/Line/Mode/{MODES}/Status"
LINE_STOPS_URL = "https://api.tfl.gov.uk/Line/{line_id}/StopPoints"
STOP_URL = "https://api.tfl.gov.uk/StopPoint/{ids}"
# Longer id lists make the request URL exceed the upstream length limit (HTTP 400).
STOP_BATCH = 20

# The single place that decides which items are kept. Owner's rule: keep only
# emergencies and incidents that bring police, ambulance or fire crews to a
# station. Each entry: cause label (event title), pattern searched in the item
# text (case-insensitive), event category, severity. Entries are ordered by
# descending severity and the first matching entry is used. A cause that matches
# no entry is dropped.
#
# Deliberately absent, because they are operational causes and not emergencies:
# signal failure, faulty train, track fault, power failure, staff shortage,
# strike / industrial action, planned works, train cancellations, overcrowding
# and crowd control for events, faulty lifts and escalators.
#
# Patterns are phrases, not single words: "fire" alone would match "fire
# brigade strike", "emergency" alone would match "emergency engineering work".
#
# UNVERIFIED: on 2026-09-19 neither feed listed an incident, so no phrase below
# was observed live. They are TfL's customary status wording written from
# memory. Check them against the raw_items of the first real incidents.
#
# No Category value describes an emergency at a station; TRANSIT_DISRUPTION is
# the closest existing value, FIRE and VIOLENT_CRIME where the cause says so.
#
# REVISIT(severity-scale): the severities are provisional values.
INCIDENT_CAUSES: list[tuple[str, re.Pattern[str], Category, float]] = [
    (label, re.compile(pattern, re.IGNORECASE), category, severity)
    for label, pattern, category, severity in [
        (
            "Casualty on the track",
            r"casualty on the (?:track|line)|person (?:on|under|hit by|struck by) (?:the|a) (?:track|line|train)",
            Category.TRANSIT_DISRUPTION,
            0.5,
        ),
        ("Assault", r"\bassault", Category.VIOLENT_CRIME, 0.5),
        (
            "Fire alert",
            r"fire alert|fire investigation|fire brigade investigat|reports? of (?:a )?(?:fire|smoke)|\bsmoke\b",
            Category.FIRE,
            0.4,
        ),
        (
            "Police incident",
            r"police (?:investigation|incident|request|activity|operation|respond|deal)"
            r"|request of the (?:british transport )?police",
            Category.TRANSIT_DISRUPTION,
            0.4,
        ),
        (
            "Security alert",
            r"security (?:alert|incident)|unattended (?:item|bag|package|luggage)"
            r"|(?:suspect|suspicious) (?:package|item)",
            Category.TRANSIT_DISRUPTION,
            0.4,
        ),
        ("Trespasser", r"trespass", Category.TRANSIT_DISRUPTION, 0.4),
        ("Evacuation", r"evacuat", Category.TRANSIT_DISRUPTION, 0.4),
        (
            "Emergency services incident",
            r"emergency services? (?:are |is )?(?:dealing|attending|respond|incident)",
            Category.TRANSIT_DISRUPTION,
            0.4,
        ),
        (
            "Customer incident",
            r"customer incident|passenger incident|medical (?:emergency|incident)"
            r"|(?:person|customer|passenger) (?:taken )?ill\b|\bill (?:customer|passenger|person)",
            Category.TRANSIT_DISRUPTION,
            0.3,
        ),
    ]
]

# An incident is unplanned. Station items carry `appearance` and line statuses
# carry `disruption.category`; only this value can be an incident. "PlannedWork"
# and "Information" items mention police or evacuation only as standing advice.
REALTIME = "RealTime"

# "due to an earlier customer incident": the incident is over and only the
# service is still recovering. Such a mention is not a match.
_EARLIER = re.compile(r"\bearlier\s+(?:\w+\s+){0,2}$", re.IGNORECASE)

# Two incident items with the same cause closer than this are one incident
# reported more than once: by several lines, by the station feed and the line
# feed, or by the tube and rail stops of one station.
DUPLICATE_DISTANCE_M = 400.0

# In a line status reason, the station follows the cause: "<cause> at <station>",
# with at most this many words in between ("person ill on a train at ...").
_AT = re.compile(r"^(?:\W+\w+){0,4}?\W+at\s+", re.IGNORECASE)
MAX_STATION_WORDS = 7

# atcoCode -> (lat, lon). None when upstream does not know the stop.
_STOP_COORDS: dict[str, tuple[float, float] | None] = {}
# line id -> normalised station name -> [(stop id, name, lat, lon)]
_LINE_STOPS: dict[str, dict[str, list[tuple[str, str, float, float]]]] = {}


def classify(d: dict[str, Any]) -> tuple[str, Category, float, int] | None:
    """Cause label, category, severity and the end offset of the matched phrase in
    `description`. None when the item is not an incident."""
    if d.get("appearance") != REALTIME:
        return None
    text = d.get("description") or ""
    for label, pattern, category, severity in INCIDENT_CAUSES:
        for m in pattern.finditer(text):
            if not _EARLIER.search(text[: m.start()]):
                return label, category, severity, m.end()
    return None


class TflTransitSource:
    id = "tfl_transit"
    snapshot = True

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        params = _params()
        resp = httpx.get(URL, params=params, timeout=30)
        resp.raise_for_status()
        disruptions = resp.json()
        resp = httpx.get(LINE_STATUS_URL, params=params, timeout=30)
        resp.raise_for_status()
        lines = resp.json()

        # Coordinates are needed only for items that can produce an event
        _resolve_stops({d["atcoCode"] for d in disruptions if d.get("atcoCode") and classify(d)}, params)
        for line_id in {p["lineId"] for p in _line_incidents(lines)}:
            if line_id not in _LINE_STOPS:
                _LINE_STOPS[line_id] = _line_stops(line_id, params)
        return self.items(disruptions, lines), cursor

    def items(self, disruptions: list[dict[str, Any]], lines: list[dict[str, Any]] = ()) -> list[RawItem]:
        """Embed cached coordinates, assign external ids and mark duplicates.

        Every station item becomes a RawItem, also the items that to_event() drops.
        run_poll skips end_missing when a snapshot fetch returns no items. Incidents
        are rare, so a result reduced to the kept items would usually be empty and
        the stored incidents would never be marked ended. Line statuses become
        RawItems only when they are located incidents.
        """
        base_ids = [_base_id(d) for d in disruptions]
        counts = Counter(base_ids)
        items = []
        for d, base in zip(disruptions, base_ids):
            if coords := _STOP_COORDS.get(d.get("atcoCode", "")):
                d = {**d, "lat": coords[0], "lon": coords[1]}
            # Two disruptions at one stop with equal type and start are told
            # apart by a hash of the description.
            ext = base if counts[base] == 1 else f"{base}:{_digest(d.get('description', ''))}"
            items.append(RawItem(source_id=self.id, external_id=ext, payload=d))

        line_items = []
        for p in _line_incidents(lines):
            stop = _locate(p, _LINE_STOPS.get(p["lineId"], {}))
            if stop is None:
                continue
            stop_id, name, lat, lon = stop
            p = {**p, "atcoCode": stop_id, "commonName": name, "lat": lat, "lon": lon}
            # The reason text changes while an incident is ongoing ("Severe delays"
            # to "Minor delays"), so it is not part of the id.
            cause = classify(p)[0]
            ext = f"line:{p['lineId']}:{cause}:{stop_id}:{(p.get('fromDate') or '')[:10]}"
            line_items.append(RawItem(source_id=self.id, external_id=ext, payload=p))
        # Two statuses of one line can name the same incident
        line_items = list({i.external_id: i for i in line_items}.values())

        _mark_duplicates(items + sorted(line_items, key=lambda i: i.external_id))
        return items + line_items

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        cause = classify(d)
        if cause is None or d.get("duplicateOf"):
            return None
        label, category, severity, _ = cause

        lng, lat = d.get("lon"), d.get("lat")
        if lng is None or lat is None or not in_london(lng, lat):
            return None

        confidence = SOURCE_TYPE_CONFIDENCE["official_feed"]
        name = (d.get("commonName") or d.get("atcoCode") or "").strip()
        return Event(
            external_ref=external_ref(raw),
            category=category,
            title=f"{label}: {name}"[:200],
            summary=d.get("description"),
            geometry={"type": "Point", "coordinates": [lng, lat]},
            lng=lng,
            lat=lat,
            radius_m=CATEGORY_DEFAULTS[category].radius_m,
            severity=severity,
            confidence=confidence,
            source_confidence={self.id: confidence},
            half_life_min=None,
            occurred_at=_parse_dt(d.get("fromDate")) or utcnow(),
            expires_at=_parse_dt(d.get("toDate")),
            source_ids=[self.id],
            urls=[],
        )


def _line_incidents(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Line statuses whose reason names an incident cause, as payloads with the
    field names of a station item (`description`, `appearance`, `fromDate`, `toDate`)."""
    found = []
    for line in lines:
        for status in line.get("lineStatuses") or []:
            period = (status.get("validityPeriods") or [{}])[0]
            payload = {
                "lineId": line.get("id"),
                "lineName": line.get("name"),
                "mode": line.get("modeName"),
                "statusSeverityDescription": status.get("statusSeverityDescription"),
                "description": (status.get("reason") or "").strip(),
                "appearance": (status.get("disruption") or {}).get("category"),
                "fromDate": period.get("fromDate"),
                "toDate": period.get("toDate"),
            }
            if payload["lineId"] and classify(payload):
                found.append(payload)
    return found


def _locate(
    payload: dict[str, Any], stops: dict[str, list[tuple[str, str, float, float]]]
) -> tuple[str, str, float, float] | None:
    """The station named after the cause in a line status reason. None unless the
    text equals exactly one station name of the line after normalisation."""
    rest = payload["description"][classify(payload)[3] :]
    at = _AT.match(rest)
    if at is None:
        return None
    words = rest[at.end() :].split()[:MAX_STATION_WORDS + 1]
    # Longest name first, so that "Acton Town" is not read as "Acton"
    for n in range(min(len(words), MAX_STATION_WORDS), 0, -1):
        matches = stops.get(_normalise(" ".join(words[:n])))
        if not matches:
            continue
        # A capitalised word after the name means the text names a longer place
        # ("Hampstead Heath" on a line that only has "Hampstead"): no match.
        ended = words[n - 1][-1] in ".,;:"
        if n < len(words) and not ended and words[n][0].isupper():
            return None
        # Several stops with one name are accepted when they are one station
        # ("Paddington" and "Paddington (H&C Line)")
        first = matches[0]
        if all(_distance_m(first[2], first[3], m[2], m[3]) <= DUPLICATE_DISTANCE_M for m in matches):
            return first
        return None
    return None


def _normalise(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", name.lower()).replace("&", " and ")
    n = re.sub(r"[^a-z0-9 ]", "", re.sub(r"[-/]", " ", n))
    words = n.split()
    while words and words[-1] in {"station", "underground", "rail", "dlr"}:
        words.pop()
    return " ".join(words)


def _line_stops(line_id: str, params: dict[str, str]) -> dict[str, list[tuple[str, str, float, float]]]:
    resp = httpx.get(LINE_STOPS_URL.format(line_id=line_id), params=params, timeout=30)
    resp.raise_for_status()
    return _stop_table(resp.json())


def _stop_table(stops: list[dict[str, Any]]) -> dict[str, list[tuple[str, str, float, float]]]:
    table: dict[str, list[tuple[str, str, float, float]]] = {}
    for stop in stops:
        if stop.get("lat") and stop.get("lon") and stop.get("commonName"):
            entry = (stop.get("naptanId") or stop.get("id"), stop["commonName"], stop["lat"], stop["lon"])
            table.setdefault(_normalise(stop["commonName"]), []).append(entry)
    return table


def _mark_duplicates(items: list[RawItem]) -> None:
    """Set payload["duplicateOf"] on every located incident item that repeats an
    earlier one in `items` (same cause, within DUPLICATE_DISTANCE_M)."""
    kept: list[tuple[str, float, float, str]] = []
    for item in items:
        d = item.payload
        cause = classify(d)
        if cause is None or d.get("lat") is None or d.get("lon") is None:
            continue
        for label, lat, lon, ext in kept:
            if label == cause[0] and _distance_m(lat, lon, d["lat"], d["lon"]) <= DUPLICATE_DISTANCE_M:
                item.payload = {**d, "duplicateOf": ext}
                break
        else:
            kept.append((cause[0], d["lat"], d["lon"], item.external_id))


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular approximation, accurate to well under 1 % at these distances."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6_371_000 * math.hypot(x, y)


def _params() -> dict[str, str]:
    params = {}
    if key := os.environ.get("TFL_APP_KEY"):
        params["app_key"] = key
    return params


def _base_id(d: dict[str, Any]) -> str:
    stop = d.get("atcoCode") or d.get("stationAtcoCode")
    if not stop:
        return _digest(d.get("description", ""))
    return f"{stop}:{d.get('type', '')}:{d.get('fromDate', '')}"


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _resolve_stops(atco_codes: set[str], params: dict[str, str]) -> None:
    """Fill _STOP_COORDS for every code not already cached."""
    missing = sorted(atco_codes - _STOP_COORDS.keys())
    for i in range(0, len(missing), STOP_BATCH):
        batch = missing[i : i + STOP_BATCH]
        found = _lookup(batch, params)
        for code in batch:
            if code in found:
                _STOP_COORDS[code] = found[code]
    # A batch response replaces a station that belongs to an interchange hub
    # with the hub record, and not every station is listed among the hub's
    # children. A single lookup returns the same hub, whose own coordinates
    # are then used.
    for code in missing:
        if code not in _STOP_COORDS:
            found = _lookup([code], params)
            _STOP_COORDS[code] = found.get(code) or found.get("")


def _lookup(codes: list[str], params: dict[str, str]) -> dict[str, tuple[float, float]]:
    """Coordinates by stop id for every stop in the response tree.

    For a single-code request the key "" holds the top-level record's
    coordinates. An unknown code (HTTP 404) gives an empty result.
    """
    resp = httpx.get(STOP_URL.format(ids=",".join(codes)), params=params, timeout=30)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    body = resp.json()
    found: dict[str, tuple[float, float]] = {}
    if isinstance(body, dict):
        if body.get("lat") and body.get("lon"):
            found[""] = (body["lat"], body["lon"])
        body = [body]
    for stop in _walk(body):
        if stop.get("lat") and stop.get("lon"):
            found.setdefault(stop.get("naptanId") or stop.get("id"), (stop["lat"], stop["lon"]))
    return found


def _walk(stops: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for stop in stops:
        yield stop
        yield from _walk(stop.get("children") or [])


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
