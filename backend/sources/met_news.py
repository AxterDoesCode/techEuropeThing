"""Metropolitan Police newsroom RSS (official statements and appeals).

Unstructured text: each item goes through the pre-filter, an extractor and the
geocoder. Until the LLM extraction agent is configured, the rule-based
extractor is used.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .. import extract_rules
from ..geocode import geocode
from ..models import SOURCE_TYPE_CONFIDENCE, Event, RawItem
from ..prefilter import is_incident_candidate
from .base import external_ref

URL = "https://news.met.police.uk/rss/current_news/66871"
# Statements are published hours after the incident, so a longer half-life than
# the category default is used; otherwise every event would arrive already decayed.
HALF_LIFE_MIN = 24 * 60
# Geocode matches wider than this are area-level (a district, not a street)
STREET_LEVEL_M = 400.0
AREA_CONFIDENCE_FACTOR = 0.6


def _text(value: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


class MetNewsSource:
    id = "met_news"
    snapshot = False

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        resp = httpx.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        resp.raise_for_status()
        items = []
        for it in ET.fromstring(resp.content).iter("item"):
            payload = {
                "title": _text(it.findtext("title")),
                "description": _text(it.findtext("description")),
                "link": it.findtext("link"),
                "guid": it.findtext("guid") or it.findtext("link"),
                "pub_date": it.findtext("pubDate"),
            }
            items.append(RawItem(source_id=self.id, external_id=payload["guid"], payload=payload))
        return items, cursor

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        if not is_incident_candidate(d["title"], d["description"]):
            return None
        extracted = extract_rules.extract(d["title"], d["description"])
        if extracted is None:
            return None
        place = geocode(extracted.place_text)
        if place is None:
            return None

        confidence = SOURCE_TYPE_CONFIDENCE["official_statement"]
        if place.precision_m > STREET_LEVEL_M:
            confidence *= AREA_CONFIDENCE_FACTOR
        return Event(
            external_ref=external_ref(raw),
            category=extracted.category,
            title=d["title"][:200],
            summary=d["description"] or None,
            geometry={"type": "Point", "coordinates": [place.lng, place.lat]},
            lng=place.lng,
            lat=place.lat,
            radius_m=max(250.0, place.precision_m),
            severity=extracted.severity,
            confidence=confidence,
            source_confidence={self.id: confidence},
            half_life_min=HALF_LIFE_MIN,
            occurred_at=parsedate_to_datetime(d["pub_date"]),
            source_ids=[self.id],
            urls=[d["link"]] if d.get("link") else [],
        )
