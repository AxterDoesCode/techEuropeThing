"""Official wide-area alerts: Met Office weather warnings and UK Emergency Alerts.

These are not events. They cover large parts of London (or all of it), so they
take no part in scoring or routing; the client only states that one is in force.
They are stored in the `alerts` table and served by GET /api/alerts (api_alerts.py).

Feeds (no key needed):
  Met Office National Severe Weather Warnings, region London & South East England
    https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/se
    RSS 2.0; zero items when no warning is in force. The Met Office terms require
    attribution and a link directly to the warning (the item's <link>).
  UK Emergency Alerts
    https://www.gov.uk/alerts/feed.atom
    Atom; every alert since 2021, current and past together, including operator
    tests. Open Government Licence.

The parsing functions take the feed content and do no I/O. `poll` fetches both
feeds and replaces the stored alerts of each source whose fetch succeeded.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Literal, Protocol
from urllib.parse import parse_qs

from pydantic import BaseModel

from .models import utcnow

MET_OFFICE = "met_office"
EMERGENCY_ALERTS = "uk_emergency_alerts"
SOURCES = (MET_OFFICE, EMERGENCY_ALERTS)
SOURCE_LABELS = {MET_OFFICE: "Met Office", EMERGENCY_ALERTS: "GOV.UK Emergency Alerts"}

MET_OFFICE_URL = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/se"
EMERGENCY_ALERTS_URL = "https://www.gov.uk/alerts/feed.atom"

# The dispatcher runs once a minute and its start time varies by a few seconds, so
# the interval is slightly below 5 minutes to make every fifth run start a poll.
POLL_INTERVAL_S = 285

Level = Literal["yellow", "amber", "red"]
LEVEL_RANK: dict[str, int] = {"yellow": 1, "amber": 2, "red": 3}


class Alert(BaseModel):
    id: str  # "<source>:<upstream id>"
    source: str
    level: Level
    hazard: str
    headline: str
    area_text: str
    url: str
    starts_at: datetime
    ends_at: datetime | None = None
    raw: Any = None


# Met Office


_MET_TITLE = re.compile(r"^\s*(Yellow|Amber|Red)\s+warnings?\s+of\s+(.+?)\s+affecting\s+(.+?)\s*$", re.I)
# "0800 Thu 13 Aug": HHMM, weekday, day of month, month. No year and no timezone.
_MET_STAMP = r"(\d{2})(\d{2})\s+[A-Za-z]{3,9}\s+(\d{1,2})\s+([A-Za-z]{3,9})"
_MET_VALID = re.compile(rf"\s+valid\s+from\s+{_MET_STAMP}\s+to\s+{_MET_STAMP}\s*$", re.I)
_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _met_link_params(link: str) -> dict[str, str]:
    """Query parameters of a warning link. They follow "#?", so they are part of
    the URL fragment and urlsplit().query is empty."""
    _, _, query = link.partition("?")
    return {k: v[0] for k, v in parse_qs(query).items()}


def _met_time(hh: str, mm: str, day: str, month: str, near: date) -> datetime:
    """The feed gives no year: the year is the one that puts the date closest to
    `near`, the `date=` parameter of the item's link, so a warning that runs over
    New Year resolves correctly. The feed gives no timezone either. The times are
    read as UTC; this is an inference from two archived August 2026 items whose end
    time is "2259", which is 23:59 BST, the usual end of a warning day. No winter
    item was available to confirm it."""
    candidates = []
    for year in (near.year - 1, near.year, near.year + 1):
        try:
            candidates.append(datetime(year, _MONTHS[month[:3].lower()], int(day), int(hh), int(mm), tzinfo=timezone.utc))
        except ValueError:  # 29 Feb in a non-leap year
            continue
    return min(candidates, key=lambda dt: abs(dt.date() - near))


def parse_met_item(title: str, description: str, link: str) -> dict[str, Any] | None:
    """One RSS item -> level, hazard, areas, starts_at, ends_at, upstream id.
    None when the item does not have the expected structure."""
    m = _MET_TITLE.match(title)
    valid = _MET_VALID.search(description)
    params = _met_link_params(link)
    found = _UUID.search(params.get("id", ""))
    if m is None or valid is None or found is None:
        return None
    try:
        near = date.fromisoformat(params.get("date", ""))
        starts_at = _met_time(*valid.group(1, 2, 3, 4), near=near)
        ends_at = _met_time(*valid.group(5, 6, 7, 8), near=near)
    except (ValueError, KeyError):
        return None
    # description = title + ": " + comma-separated areas + " valid from ..."
    areas_text = description[: valid.start()]
    _, sep, after = areas_text.partition(": ")
    areas = [a.strip() for a in (after if sep else areas_text).split(",") if a.strip()]
    return {
        "upstream_id": found.group(0).lower(),
        "level": m.group(1).lower(),
        "hazard": m.group(2).strip().lower(),
        "areas": areas,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def parse_met_office(content: bytes | str) -> list[Alert]:
    """All warnings in the regional feed, London or not (see `covers_london`).
    A warning that spans several days appears as one item per day with the same
    `id=<uuid>` in the link: the items are grouped by that id, the validity is
    min(start)..max(end), and the link kept is that of the earliest day."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for it in ET.fromstring(content).iter("item"):
        title = html.unescape(it.findtext("title") or "").strip()
        description = html.unescape(it.findtext("description") or "").strip()
        link = (it.findtext("link") or "").strip()
        parsed = parse_met_item(title, description, link)
        if parsed is None:
            continue
        enclosure = it.find("enclosure")
        parsed |= {
            "title": title,
            "description": description,
            "link": link,
            "image": enclosure.get("url") if enclosure is not None else None,
        }
        groups.setdefault(parsed["upstream_id"], []).append(parsed)

    alerts = []
    for upstream_id, items in groups.items():
        items.sort(key=lambda p: p["starts_at"])
        first = items[0]
        areas = list(dict.fromkeys(a for p in items for a in p["areas"]))
        alerts.append(
            Alert(
                id=f"{MET_OFFICE}:{upstream_id}",
                source=MET_OFFICE,
                level=max((p["level"] for p in items), key=LEVEL_RANK.__getitem__),
                hazard=first["hazard"],
                headline=first["title"],
                area_text=", ".join(areas),
                url=first["link"],
                starts_at=first["starts_at"],
                ends_at=max(p["ends_at"] for p in items),
                raw=[{k: p[k] for k in ("title", "description", "link", "image")} for p in items],
            )
        )
    return alerts


# UK Emergency Alerts


_ATOM = {"a": "http://www.w3.org/2005/Atom"}
# "[2026-08-14 19:01 BST]"
_EA_STAMP = r"\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) (BST|GMT)\]"
_EA_SENT = re.compile(rf"Sent:[^\[]*{_EA_STAMP}")
_EA_STOPPED = re.compile(rf"Stopped:[^\[]*{_EA_STAMP}")
_EA_STOPPED_LINE = re.compile(r"^\s*Stopped sending at\b", re.I)
# Operator tests are published in the same feed. "prawf" is the Welsh wording.
_EA_TEST = re.compile(r"\b(test|tests|testing|prawf)\b", re.I)
_EA_ISSUER = re.compile(r"^(Issued by|From)\b[^.\n]*[.\n]\s*", re.I)
HEADLINE_MAX = 200


def _ea_time(groups: tuple[str, ...]) -> datetime:
    y, mo, d, h, mi, zone = groups
    offset = timedelta(hours=1 if zone == "BST" else 0)
    return datetime(int(y), int(mo), int(d), int(h), int(mi), tzinfo=timezone(offset)).astimezone(timezone.utc)


def _ea_message(entry: ET.Element) -> str:
    """Message text of an entry: the <p> elements of <content> without the
    "Stopped sending at ..." line."""
    content = entry.find("a:content", _ATOM)
    if content is None:
        return ""
    paragraphs = ["".join(p.itertext()).strip() for p in content.iter("{http://www.w3.org/1999/xhtml}p")]
    return "\n\n".join(p for p in paragraphs if p and not _EA_STOPPED_LINE.match(p))


def _ea_headline(message: str) -> str:
    """First sentence of the message after the issuer line ("Issued by ...")."""
    text = _EA_ISSUER.sub("", message.strip(), count=1)
    first = re.split(r"(?<=[.!?])\s+|\n", text, maxsplit=1)[0].strip()
    return first if len(first) <= HEADLINE_MAX else first[: HEADLINE_MAX - 1].rstrip() + "…"


def parse_emergency_alerts(content: bytes | str, now: datetime) -> list[Alert]:
    """Alerts that are being broadcast at `now`. Excluded: entries whose "Stopped"
    stamp is not in the future, and operator tests. The feed has no level field;
    every entry is a broadcast about a danger to life, so the level is "red"."""
    alerts = []
    for entry in ET.fromstring(content).findall("a:entry", _ATOM):
        entry_id = (entry.findtext("a:id", namespaces=_ATOM) or "").strip()
        title = " ".join((entry.findtext("a:title", namespaces=_ATOM) or "").split())
        summary = entry.findtext("a:summary", namespaces=_ATOM) or ""
        message = _ea_message(entry)
        if not entry_id or _EA_TEST.search(message):
            continue
        stopped = _EA_STOPPED.search(summary)
        ends_at = _ea_time(stopped.groups()) if stopped else None
        if ends_at is not None and ends_at <= now:
            continue
        sent = _EA_SENT.search(summary)
        published = entry.findtext("a:published", namespaces=_ATOM)
        if sent:
            starts_at = _ea_time(sent.groups())
        elif published:
            starts_at = datetime.fromisoformat(published.strip()).astimezone(timezone.utc)
        else:
            continue
        link = entry.find("a:link", _ATOM)
        alerts.append(
            Alert(
                id=f"{EMERGENCY_ALERTS}:{entry_id.rstrip('/').rsplit('/', 1)[-1]}",
                source=EMERGENCY_ALERTS,
                level="red",
                hazard="emergency alert",
                headline=_ea_headline(message),
                area_text=title,
                url=(link.get("href") if link is not None else None) or entry_id,
                starts_at=starts_at,
                ends_at=ends_at,
                raw={"id": entry_id, "title": title, "summary": summary, "message": message},
            )
        )
    return alerts


# London coverage


LONDON_BOROUGHS = (
    "Barking and Dagenham", "Barnet", "Bexley", "Brent", "Bromley", "Camden", "Croydon", "Ealing",
    "Enfield", "Greenwich", "Hackney", "Hammersmith and Fulham", "Haringey", "Harrow", "Havering",
    "Hillingdon", "Hounslow", "Islington", "Kensington and Chelsea", "Kingston upon Thames", "Lambeth",
    "Lewisham", "Merton", "Newham", "Redbridge", "Richmond upon Thames", "Southwark", "Sutton",
    "Tower Hamlets", "Waltham Forest", "Wandsworth", "Westminster",
)
_EA_LONDON_AREAS = {
    a.lower()
    for a in ("England", "United Kingdom", "UK", "London", "Greater London", "City of London",
              "City of Westminster", *LONDON_BOROUGHS)
}


def _area_tokens(area_text: str) -> set[str]:
    return {t.strip().lower() for t in area_text.split(",") if t.strip()}


def covers_london(source: str, area_text: str) -> bool:
    """Met Office: the `se` region also contains Kent, Surrey, Oxfordshire etc., so
    the area list must name "Greater London". Emergency Alerts: the area text is a
    comma-separated list of nations, local authorities or flood-area names; it
    covers London when one whole list entry is England, the UK, London or a London
    borough. Entries are compared whole, so "Newport" or "Londonderry" do not match."""
    tokens = _area_tokens(area_text)
    if source == MET_OFFICE:
        return "greater london" in tokens
    return not tokens.isdisjoint(_EA_LONDON_AREAS)


# Polling


def _get(url: str) -> bytes:
    import httpx

    resp = httpx.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def fetch_met_office(now: datetime) -> list[Alert]:
    return parse_met_office(_get(MET_OFFICE_URL))


def fetch_emergency_alerts(now: datetime) -> list[Alert]:
    return parse_emergency_alerts(_get(EMERGENCY_ALERTS_URL), now)


Fetcher = Callable[[datetime], list[Alert]]
FETCHERS: dict[str, Fetcher] = {MET_OFFICE: fetch_met_office, EMERGENCY_ALERTS: fetch_emergency_alerts}


class AlertRepo(Protocol):
    def replace_alerts(self, source: str, alerts: list[Alert], fetched_at: datetime) -> dict[str, int]: ...
    def record_alerts_failure(self, source: str, error: str, at: datetime) -> None: ...


def poll(repo: AlertRepo, now: datetime | None = None, fetchers: dict[str, Fetcher] | None = None) -> dict[str, Any]:
    """Fetch every feed and replace the stored alerts of each source that was
    fetched and parsed. A failure (HTTP error, invalid XML) is recorded and leaves
    that source's stored alerts as they are."""
    now = now or utcnow()
    result: dict[str, Any] = {}
    for source, fetch in (fetchers or FETCHERS).items():
        try:
            alerts = fetch(now)
        except Exception as exc:  # one feed failing must not stop the other
            error = f"{type(exc).__name__}: {exc}"[:500]
            repo.record_alerts_failure(source, error, now)
            result[source] = {"error": error}
            continue
        result[source] = repo.replace_alerts(source, alerts, now)
    return result
