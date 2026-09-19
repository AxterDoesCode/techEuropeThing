"""Extraction bookkeeping and the parallel item mapper in run_poll."""

import pytest

from backend import db, extraction, geocode
from backend.db import SqliteRepo
from backend.geocode import GeoResult
from backend.models import SOURCE_TYPE_CONFIDENCE, RawItem
from backend.pipeline import SOURCES, is_pollable, run_poll
from backend.sources.rss import RssNewsSource

STABBING = "Man dies following stabbing in Lloyd Baker Street, Clerkenwell"
COURT = "Man jailed for life following murder in Westminster"


def raw(guid: str, title: str) -> RawItem:
    payload = {"title": title, "description": "", "link": f"https://example.org/{guid}", "guid": guid,
               "pub_date": "Fri, 18 Sep 2026 17:37:00 +0100"}
    return RawItem(source_id="feed", external_id=guid, payload=payload)


class Feed(RssNewsSource):
    def __init__(self, items: list[RawItem]) -> None:
        super().__init__("feed", "u", "news")
        self.items = items
        self.extracted: list[str] = []

    def fetch(self, cursor):
        return self.items, cursor

    def to_events(self, raw_item, lookup=None):
        self.extracted.append(raw_item.external_id)
        return super().to_events(raw_item, lookup)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(extraction, "geocode", lambda _: GeoResult(-0.1104, 51.5289, 150.0, "x", "test"))
    db.connect(tmp_path / "risk.sqlite")
    with db._tx() as conn:
        conn.execute("insert into sources (id, kind, poll_interval_s) values ('feed', 'unstructured', 600)")
    return SqliteRepo()


def test_only_candidates_are_extracted_and_only_once(repo):
    feed = Feed([raw("a", STABBING), raw("b", COURT)])
    assert run_poll(feed, repo)["inserted"] == 1
    assert feed.extracted == ["a"]

    # nothing changed upstream: the second poll starts no extraction
    assert run_poll(feed, repo)["inserted"] == 0
    assert feed.extracted == ["a"]

    # a new item is extracted, the old one is not repeated
    feed.items.append(raw("c", "Woman injured in assault in Camden High Street"))
    run_poll(feed, repo)
    assert feed.extracted == ["a", "c"]


def test_edited_article_is_extracted_again(repo):
    feed = Feed([raw("a", STABBING)])
    run_poll(feed, repo)
    feed.items[0] = raw("a", STABBING + " (updated)")
    run_poll(feed, repo)
    assert feed.extracted == ["a", "a"]


def test_changing_the_extractor_reprocesses_items(repo, monkeypatch):
    feed = Feed([raw("a", STABBING)])
    run_poll(feed, repo)
    assert repo.pending_extraction("feed", ["a"], "rules") == []
    assert repo.pending_extraction("feed", ["a"], "google-gla:gemini-2.5-flash") == ["a"]


def test_map_items_replaces_the_sequential_loop(repo):
    feed = Feed([raw("a", STABBING), raw("b", COURT), raw("c", "Shooting in Brixton Road, Brixton")])
    seen: list[list[str]] = []

    def mapper(items):
        seen.append([i.external_id for i in items])
        return [RssNewsSource.to_events(feed, i, None) for i in items]

    counts = run_poll(feed, repo, map_items=mapper)
    assert seen == [["a", "c"]]
    assert counts["inserted"] == 1  # same geocoded place and time: the second report merges
    assert counts["merged"] == 1
    assert feed.extracted == []  # the source's own loop was not used
    assert all(ev.raw_item_ids for ev in db.events_geojson(db.utcnow()))


def test_failed_extraction_leaves_items_pending(repo):
    feed = Feed([raw("a", STABBING)])

    def failing(items):
        raise TimeoutError("extraction containers unavailable")

    with pytest.raises(RuntimeError):
        run_poll(feed, repo, map_items=failing)
    assert repo.pending_extraction("feed", ["a"], "rules") == ["a"]


def test_remote_geocoder_is_used_when_set(monkeypatch):
    monkeypatch.setattr(geocode, "_cache", {})
    calls = []
    geocode.use_remote(lambda text: calls.append(text) or GeoResult(0.0, 51.5, 100.0, "x", "remote"))
    try:
        assert geocode.geocode("Brixton Road").provider == "remote"
        assert calls == ["Brixton Road"]
    finally:
        geocode.use_remote(None)


def test_news_feeds_need_an_llm_and_social_confidence_exists(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert {"bbc_london", "standard_london", "mylondon"} <= set(SOURCES)
    assert not any(is_pollable(s) for s in ("bbc_london", "standard_london", "mylondon"))
    assert is_pollable("met_news")
    monkeypatch.setenv("LLM_MODEL", "google-gla:gemini-2.5-flash")
    assert all(is_pollable(s) for s in ("bbc_london", "standard_london", "mylondon"))
    assert 0 < SOURCE_TYPE_CONFIDENCE["social"] < SOURCE_TYPE_CONFIDENCE["manual"]


def test_failed_item_stays_pending_and_others_are_kept(repo):
    feed = Feed([raw("a", STABBING), raw("c", "Shooting in Brixton Road, Brixton")])

    def mapper(items):
        return [None, RssNewsSource.to_events(feed, items[1], None)]

    counts = run_poll(feed, repo, map_items=mapper)
    assert counts["failed"] == 1 and counts["inserted"] == 1
    assert repo.pending_extraction("feed", ["a", "c"], "rules") == ["a"]


def test_llm_only_feed_does_not_fall_back_to_rules(repo, monkeypatch):
    class LlmOnly(Feed):
        requires_llm = True

    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(extraction.llm, "run_counted", lambda deps: (None, extraction.llm.ExtractionUsage(1, 0, 0, 0), TimeoutError("quota")))
    feed = LlmOnly([raw("a", STABBING)])
    counts = run_poll(feed, repo)
    assert counts["failed"] == 1 and counts["inserted"] == 0
    assert repo.pending_extraction("feed", ["a"], "test") == ["a"]

    # a feed that allows the fallback still produces the rule-based event
    assert run_poll(Feed([raw("a", STABBING)]), repo)["inserted"] == 1
