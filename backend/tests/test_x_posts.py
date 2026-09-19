"""X source. The twscrape call (`x_posts._search`) is replaced by fixed data:
no X account is used in tests. The post texts are invented."""

from datetime import timedelta

import pytest

from backend import db, extraction
from backend.db import SqliteRepo
from backend.geocode import GeoResult
from backend.llm import article_prompt
from backend.pipeline import SOURCES, is_pollable, run_poll
from backend.sources import x_posts
from backend.sources.x_posts import MAX_POSTS_PER_POLL, XSource, to_raw_items

POSTS = [
    {"id": 1002, "text": "Police cordon on Atlantic Road, Brixton.\nArmed police everywhere, avoid the area",
     "url": "https://x.com/someone/status/1002", "date": "2026-09-19T16:05:00+00:00", "place": "Brixton, London"},
    {"id": 1001, "text": "Anyone know why the shooting scene in that new film was cut? #London",
     "url": "https://x.com/someone/status/1001", "date": "2026-09-19T16:01:00+00:00", "place": None},
]


def test_to_raw_items_maps_fields_and_keeps_the_newest():
    items = to_raw_items(POSTS, "x_london")
    assert [i.external_id for i in items] == ["1002", "1001"]
    first = items[0].payload
    assert first["title"].startswith("Police cordon on Atlantic Road, Brixton. Armed police")
    assert first["description"].endswith("(Tagged place: Brixton, London)")
    assert first["link"] == "https://x.com/someone/status/1002"
    many = [POSTS[0] | {"id": i} for i in range(100)]
    kept = to_raw_items(many, "x_london")
    assert len(kept) == MAX_POSTS_PER_POLL and kept[0].external_id == "99"


def test_article_is_social_and_timezone_aware():
    src = XSource()
    article = src.article(to_raw_items(POSTS, src.id)[0])
    assert article.kind == "social" and article.published_at.utcoffset() == timedelta(0)
    assert "Item type: social" in article_prompt(article)


def test_fetch_passes_and_advances_since_id(monkeypatch):
    seen = []

    def fake_search(queries, since_id):
        seen.append(since_id)
        return POSTS if since_id is None else []

    monkeypatch.setattr(x_posts, "_search", fake_search)
    src = XSource()
    items, cursor = src.fetch({})
    assert len(items) == 2 and cursor == {"since_id": 1002}
    items, cursor = src.fetch(cursor)
    assert items == [] and cursor == {"since_id": 1002}
    assert seen == [None, 1002]


def test_source_needs_llm_and_cookies(monkeypatch):
    assert "x_london" in SOURCES and XSource().source_type == "social"
    monkeypatch.setenv("LLM_MODEL", "google:gemini-3.8-flash")
    monkeypatch.delenv("X_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("X_CT0", raising=False)
    assert not is_pollable("x_london")
    monkeypatch.setenv("X_AUTH_TOKEN", "a")
    assert not is_pollable("x_london")
    monkeypatch.setenv("X_CT0", "b")
    assert is_pollable("x_london")
    monkeypatch.delenv("LLM_MODEL")
    assert not is_pollable("x_london")
    # sources without credentials are unaffected
    assert is_pollable("tfl_road")


def test_poll_stores_the_cursor_and_extracts_each_post_once(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(extraction, "geocode", lambda _: GeoResult(-0.1145, 51.4627, 150.0, "x", "test"))
    calls = []

    def fake_extract(article, source_id, source_type, lookup=None, rules_fallback=True):
        calls.append((article.guid, source_type, rules_fallback))
        return [], 1

    monkeypatch.setattr("backend.sources.rss.extract_events", fake_extract)
    monkeypatch.setattr(x_posts, "_search", lambda queries, since_id: POSTS if since_id is None else [])
    db.connect(tmp_path / "risk.sqlite")
    repo = SqliteRepo()

    counts = run_poll(XSource(), repo)
    assert counts["fetched"] == 2 and counts["llm_calls"] == len(calls)
    # social confidence, and no keyword-rule fallback for this source
    assert all(source_type == "social" and fallback is False for _, source_type, fallback in calls)
    assert repo.get_cursor("x_london") == {"since_id": 1002}

    before = len(calls)
    run_poll(XSource(), repo)
    assert len(calls) == before
