"""Reddit source. fixtures/reddit_london.xml is a trimmed copy of the real Atom feed
of 2026-09-19 with author elements and the "submitted by" trailers removed."""

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from backend import extraction
from backend.geocode import GeoResult
from backend.llm import article_prompt
from backend.models import SOURCE_TYPE_CONFIDENCE
from backend.pipeline import SOURCES, is_pollable
from backend.sources import reddit
from backend.sources.reddit import RedditSource, parse_atom, parse_listing

FEED = (Path(__file__).parent / "fixtures" / "reddit_london.xml").read_bytes()

LISTING = {
    "data": {
        "children": [
            {"data": {"name": "t3_abc", "title": "Police cordon outside Brixton station right now",
                      "selftext": "Whole of  Atlantic Road taped off,\n\nloads of police.",
                      "permalink": "/r/london/comments/abc/police_cordon/", "created_utc": 1789830000,
                      "subreddit": "london", "stickied": False}},
            {"data": {"name": "t3_sticky", "title": "Weekly thread", "selftext": "", "permalink": "/r/london/x/",
                      "created_utc": 1789830000, "subreddit": "london", "stickied": True}},
        ]
    }
}


def test_parse_atom_maps_fields_and_strips_markup():
    items = parse_atom(FEED, "reddit_london")
    assert len(items) == 5
    museum = next(i for i in items if "British Museum" in i.payload["title"])
    assert museum.external_id.startswith("t3_")
    assert museum.payload["link"].startswith("https://www.reddit.com/r/")
    assert museum.payload["subreddit"]
    assert "<" not in museum.payload["description"] and "submitted by" not in museum.payload["description"]
    assert len(museum.payload["description"]) <= reddit.MAX_TEXT_CHARS


def test_parse_listing_skips_stickied_posts():
    items = parse_listing(LISTING, "reddit_london")
    assert [i.external_id for i in items] == ["t3_abc"]
    assert items[0].payload["description"] == "Whole of Atlantic Road taped off, loads of police."
    assert items[0].payload["link"] == "https://www.reddit.com/r/london/comments/abc/police_cordon/"


def test_article_is_social_and_timezone_aware():
    src = RedditSource()
    article = src.article(parse_listing(LISTING, src.id)[0])
    assert article.kind == "social" and article.source_id == "reddit_london"
    assert article.published_at.utcoffset() == timedelta(0)
    assert "Item type: social" in article_prompt(article)
    atom_article = src.article(parse_atom(FEED, src.id)[0])
    assert atom_article.published_at.utcoffset() is not None


def test_source_is_registered_llm_only_and_low_confidence(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert "reddit_london" in SOURCES and not is_pollable("reddit_london")
    assert RedditSource.requires_llm and RedditSource().source_type == "social"
    assert SOURCE_TYPE_CONFIDENCE["social"] < SOURCE_TYPE_CONFIDENCE["news"]


def test_fetch_uses_oauth_when_credentials_are_set(monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(("POST", url, kw.get("auth")))
        return httpx.Response(200, json={"access_token": "tok"}, request=httpx.Request("POST", url))

    def fake_get(url, **kw):
        calls.append(("GET", url, kw["headers"].get("Authorization")))
        return httpx.Response(200, json=LISTING, request=httpx.Request("GET", url))

    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(reddit.httpx, "post", fake_post)
    monkeypatch.setattr(reddit.httpx, "get", fake_get)
    items, _ = RedditSource(["london", "hackney"]).fetch({})
    assert [i.external_id for i in items] == ["t3_abc"]
    assert calls[0] == ("POST", "https://www.reddit.com/api/v1/access_token", ("id", "secret"))
    assert calls[1] == ("GET", "https://oauth.reddit.com/r/london+hackney/new", "Bearer tok")


def test_fetch_falls_back_to_the_atom_feed(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        return httpx.Response(200, content=FEED, request=httpx.Request("GET", url))

    monkeypatch.setattr(reddit.httpx, "get", fake_get)
    items, _ = RedditSource(["london"]).fetch({})
    assert urls == ["https://www.reddit.com/r/london/new/.rss"] and len(items) == 5


def test_rules_are_never_used_for_reddit(monkeypatch):
    """requires_llm: a failed LLM run raises instead of falling back to keyword rules."""
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(extraction, "geocode", lambda _: GeoResult(-0.1267, 51.5193, 150.0, "x", "test"))
    monkeypatch.setattr(extraction.llm, "run_counted",
                        lambda deps: (None, extraction.llm.ExtractionUsage(1, 0, 0, 0), TimeoutError("quota")))
    src = RedditSource()
    museum = next(i for i in parse_atom(FEED, src.id) if "British Museum" in i.payload["title"])
    assert src.is_candidate(museum)
    with pytest.raises(extraction.ExtractionFailed):
        src.to_events(museum)
