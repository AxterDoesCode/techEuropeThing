"""Public X (Twitter) posts about incidents in London, read with twscrape.

twscrape calls the internal search API of the X website with the session cookies
of a logged-in account (X_AUTH_TOKEN and X_CT0 in the secret). X offers no free
read API and no guest access, so this is the only no-cost route. It is outside
X's terms of service, the account can be locked at any time, and the library
stops working whenever X changes its internal API. The source is skipped while
the cookies are not configured.

Posts are unstructured and low confidence (`social`): one post matters little
until it merges with reports from other sources.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from ..located import Article
from ..models import RawItem
from .rss import RssNewsSource

INCIDENT_TERMS = (
    '(stabbing OR stabbed OR shooting OR "shots fired" OR "armed police" OR "police cordon" '
    'OR cordoned OR evacuated OR "on fire" OR explosion OR robbery OR mugged OR "acid attack")'
)
FILTERS = "-filter:retweets -filter:replies lang:en"
QUERIES = [
    # posts that name London
    f"{INCIDENT_TERMS} (London OR #London) {FILTERS}",
    # posts geotagged in, or by accounts located in, London
    f"{INCIDENT_TERMS} near:London within:15mi {FILTERS}",
]
POSTS_PER_QUERY = 40
# Every new post costs an LLM extraction, so a poll keeps only the newest ones
MAX_POSTS_PER_POLL = 25
ACCOUNTS_DB = "/tmp/x_accounts.db"
SEARCH_TIMEOUT_S = 60


def _search(queries: list[str], since_id: int | None) -> list[dict[str, Any]]:
    """Run the searches and return plain dicts. The only function that touches twscrape."""
    from twscrape import API

    cookies = f"auth_token={os.environ['X_AUTH_TOKEN']}; ct0={os.environ['X_CT0']}"

    async def run() -> list[dict[str, Any]]:
        api = API(pool=ACCOUNTS_DB, raise_when_no_account=True)
        if not await api.pool.get_all():
            # with cookies given, twscrape marks the account active without a login
            await api.pool.add_account("scraper", "", "", "", cookies=cookies)
        posts: dict[int, dict[str, Any]] = {}
        for q in queries:
            if since_id:
                q = f"{q} since_id:{since_id}"
            async for t in api.search(q, limit=POSTS_PER_QUERY, kv={"product": "Latest"}):
                posts[t.id] = {
                    "id": t.id,
                    "text": t.rawContent,
                    "url": t.url,
                    "date": t.date.astimezone(timezone.utc).isoformat(),
                    "place": t.place.fullName if t.place else None,
                }
        return list(posts.values())

    return asyncio.run(asyncio.wait_for(run(), SEARCH_TIMEOUT_S))


def to_raw_items(posts: list[dict[str, Any]], source_id: str) -> list[RawItem]:
    newest = sorted(posts, key=lambda p: p["id"], reverse=True)[:MAX_POSTS_PER_POLL]
    items = []
    for p in newest:
        text = " ".join(p["text"].split())
        description = f"{text} (Tagged place: {p['place']})" if p.get("place") else text
        payload = {
            "title": text[:120],
            "description": description,
            "link": p["url"],
            "guid": str(p["id"]),
            "published": p["date"],
        }
        items.append(RawItem(source_id=source_id, external_id=str(p["id"]), payload=payload))
    return items


class XSource(RssNewsSource):
    requires_llm = True
    requires_env = ("X_AUTH_TOKEN", "X_CT0")

    def __init__(self) -> None:
        super().__init__("x_london", "", "social")

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        posts = _search(QUERIES, cursor.get("since_id"))
        if posts:
            cursor = cursor | {"since_id": max(p["id"] for p in posts)}
        return to_raw_items(posts, self.id), cursor

    def article(self, raw: RawItem) -> Article:
        d = raw.payload
        return Article(
            title=d["title"],
            description=d["description"],
            url=d.get("link"),
            published_at=datetime.fromisoformat(d["published"]),
            source_id=self.id,
            guid=raw.external_id,
            kind="social",
        )
