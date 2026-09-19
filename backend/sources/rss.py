"""Generic RSS news source. Unstructured text: each item goes through
backend.extraction (pre-filter, then the LLM agent or the rule-based extractor)."""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..extraction import extract_events
from ..llm import SimilarEventLookup
from ..located import Article
from ..models import Event, RawItem


def _text(value: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", value or "")).strip()


def _pub_date(value: str | None) -> datetime | None:
    try:
        dt = parsedate_to_datetime(value or "")
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def parse_feed(content: bytes | str, source_id: str) -> list[RawItem]:
    items = []
    for it in ET.fromstring(content).iter("item"):
        payload = {
            "title": _text(it.findtext("title")),
            "description": _text(it.findtext("description")),
            "link": it.findtext("link"),
            "guid": it.findtext("guid") or it.findtext("link"),
            "pub_date": it.findtext("pubDate"),
        }
        if payload["guid"]:
            items.append(RawItem(source_id=source_id, external_id=payload["guid"], payload=payload))
    return items


class RssNewsSource:
    snapshot = False

    def __init__(self, id: str, url: str, source_type: str) -> None:
        self.id = id
        self.url = url
        # key of models.SOURCE_TYPE_CONFIDENCE
        self.source_type = source_type

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        resp = httpx.get(self.url, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
        resp.raise_for_status()
        return parse_feed(resp.content, self.id), cursor

    def article(self, raw: RawItem) -> Article:
        d = raw.payload
        return Article(
            title=d["title"],
            description=d.get("description") or "",
            url=d.get("link"),
            published_at=_pub_date(d.get("pub_date")),
            source_id=self.id,
            guid=raw.external_id,
        )

    def to_events(self, raw: RawItem, lookup: SimilarEventLookup | None = None) -> tuple[list[Event], int]:
        """Events of one item and the number of LLM requests made for it."""
        return extract_events(self.article(raw), self.id, self.source_type, lookup)

    def to_event(self, raw: RawItem) -> Event | None:
        """First event of the item. The pipeline uses `to_events`."""
        events, _ = self.to_events(raw)
        return events[0] if events else None


class BbcLondonSource(RssNewsSource):
    # General news: the rule-based extractor produced mostly false positives on
    # this feed (obituaries, court reports), so it is polled only with an LLM.
    requires_llm = True

    def __init__(self) -> None:
        super().__init__("bbc_london", "https://feeds.bbci.co.uk/news/england/london/rss.xml", "news")
