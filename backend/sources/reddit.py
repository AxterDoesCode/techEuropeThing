"""New posts from London subreddits. Unstructured and low confidence (`social`):
a post matters once it merges with reports from other sources.

Access: with REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET set, the OAuth API is used
(application-only token, about 100 requests per minute). Without them the public
Atom feed is used. Reddit answers unauthenticated clients with 429 after a few
requests and blocks unauthenticated JSON (403), so the fallback makes exactly one
request per poll and the poll interval is long.
"""

from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from ..located import Article
from ..models import RawItem
from .rss import RssNewsSource

SUBREDDITS = ["london", "croydon", "brixton", "hackney", "eastlondon", "southlondon"]
USER_AGENT = "london-live-risk-map/0.1 (hackathon project)"
ATOM = {"a": "http://www.w3.org/2005/Atom"}
MAX_TEXT_CHARS = 2000


def _plain(markup: str | None) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup or ""))
    # The Atom content ends with "submitted by /u/name [link] [comments]"
    text = re.sub(r"submitted by\s+/u/\S+.*$", "", text, flags=re.S)
    return " ".join(text.split())[:MAX_TEXT_CHARS]


def parse_atom(content: bytes | str, source_id: str) -> list[RawItem]:
    items = []
    for entry in ET.fromstring(content).findall("a:entry", ATOM):
        guid = entry.findtext("a:id", namespaces=ATOM)
        link = entry.find("a:link", ATOM)
        category = entry.find("a:category", ATOM)
        if not guid:
            continue
        payload = {
            "title": (entry.findtext("a:title", namespaces=ATOM) or "").strip(),
            "description": _plain(entry.findtext("a:content", namespaces=ATOM)),
            "link": link.get("href") if link is not None else None,
            "guid": guid,
            "published": entry.findtext("a:published", namespaces=ATOM)
            or entry.findtext("a:updated", namespaces=ATOM),
            "subreddit": category.get("term") if category is not None else None,
        }
        items.append(RawItem(source_id=source_id, external_id=guid, payload=payload))
    return items


def parse_listing(listing: dict[str, Any], source_id: str) -> list[RawItem]:
    """Posts from an OAuth API listing (`/r/<subs>/new`)."""
    items = []
    for child in listing.get("data", {}).get("children", []):
        post = child.get("data", {})
        if post.get("stickied") or post.get("removed_by_category"):
            continue
        payload = {
            "title": (post.get("title") or "").strip(),
            "description": " ".join((post.get("selftext") or "").split())[:MAX_TEXT_CHARS],
            "link": f"https://www.reddit.com{post['permalink']}" if post.get("permalink") else None,
            "guid": post["name"],
            "published": datetime.fromtimestamp(post["created_utc"], timezone.utc).isoformat(),
            "subreddit": post.get("subreddit"),
        }
        items.append(RawItem(source_id=source_id, external_id=post["name"], payload=payload))
    return items


class RedditSource(RssNewsSource):
    requires_llm = True

    def __init__(self, subreddits: list[str] | None = None) -> None:
        super().__init__("reddit_london", "", "social")
        self.subreddits = subreddits or SUBREDDITS

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        subs = "+".join(self.subreddits)
        client_id = os.environ.get("REDDIT_CLIENT_ID")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
        headers = {"User-Agent": USER_AGENT}
        if client_id and client_secret:
            token = httpx.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers=headers,
                timeout=30,
            )
            token.raise_for_status()
            resp = httpx.get(
                f"https://oauth.reddit.com/r/{subs}/new",
                params={"limit": 100, "raw_json": 1},
                headers=headers | {"Authorization": f"Bearer {token.json()['access_token']}"},
                timeout=30,
            )
            resp.raise_for_status()
            return parse_listing(resp.json(), self.id), cursor
        resp = httpx.get(
            f"https://www.reddit.com/r/{subs}/new/.rss",
            params={"limit": 50},
            headers=headers,
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return parse_atom(resp.content, self.id), cursor

    def article(self, raw: RawItem) -> Article:
        d = raw.payload
        published = datetime.fromisoformat(d["published"]) if d.get("published") else None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return Article(
            title=d["title"],
            description=d.get("description") or "",
            url=d.get("link"),
            published_at=published,
            source_id=self.id,
            guid=raw.external_id,
            kind="social",
        )
