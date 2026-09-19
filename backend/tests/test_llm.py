from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from backend import extraction, llm
from backend.geocode import GeoResult
from backend.located import HALF_LIFE_MIN, Article
from backend.models import Category, Event, RawItem, utcnow
from backend.pipeline import run_poll
from backend.sources.rss import BbcLondonSource, RssNewsSource, parse_feed
from backend.tests.test_pipeline import MemoryRepo

PUBLISHED = datetime(2026, 9, 18, 16, 37, tzinfo=timezone.utc)
PLACES = {
    "Lloyd Baker Street, Clerkenwell": GeoResult(-0.1104, 51.5289, 150, "Lloyd Baker Street", "test"),
    "Bethnal Green": GeoResult(-0.0562, 51.5303, 2600, "Bethnal Green", "test"),
    "Paris": GeoResult(2.3522, 48.8566, 150, "Paris", "test"),
}


def fake_geocoder(place_text):
    return PLACES.get(place_text)


def article(title="Man dies following stabbing in Lloyd Baker Street, Clerkenwell", url="https://news.example/a/1"):
    return Article(
        title=title, description="", url=url, published_at=PUBLISHED, source_id="met_news", guid="guid-1"
    )


def make_deps(**kwargs):
    kwargs.setdefault("article", article())
    kwargs.setdefault("geocoder", fake_geocoder)
    kwargs.setdefault("fetcher", lambda url: pytest.fail("unexpected fetch"))
    return llm.ExtractionDeps(**kwargs)


def item(place_id, **kwargs):
    base = {
        "category": "violent_crime",
        "title": "Fatal stabbing in Lloyd Baker Street",
        "summary": "A man died after a stabbing.",
        "place_id": place_id,
        "place_text": "Lloyd Baker Street, Clerkenwell",
        "severity": 0.95,
    }
    return base | kwargs


def tool_returns(messages):
    return [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]


def scripted_model(tool_calls, final_items):
    """First request: the given tool calls. Second request: the final output,
    built by `final_items(tool_returns)`."""

    def respond(messages, info):
        returns = tool_returns(messages)
        if not returns:
            return ModelResponse(parts=[ToolCallPart(name, args) for name, args in tool_calls])
        output_tool = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(output_tool, {"response": final_items(returns)})])

    return FunctionModel(respond)


# ---------------------------------------------------------------- agent runs


def test_article_with_two_incidents():
    def final(returns):
        ids = [r.content[0]["place_id"] for r in returns]
        return [
            item(ids[0]),
            item(ids[1], category="violent_crime", title="Rape in Bethnal Green", severity=0.85,
                 place_text="Bethnal Green", occurred_at="2026-09-18T02:00:00"),
        ]

    model = scripted_model(
        [("geocode", {"place_text": "Lloyd Baker Street, Clerkenwell"}), ("geocode", {"place_text": "Bethnal Green"})],
        final,
    )
    deps = make_deps()
    extracted, usage = llm.run(deps, model=model)
    assert usage.requests == 2 and usage.tool_calls == 2
    events = llm.to_events(extracted, deps, "met_news", "official_statement")

    first, second = events
    assert first.external_ref == "met_news:guid-1" and second.external_ref == "met_news:guid-1#2"
    assert (first.lng, first.lat) == (-0.1104, 51.5289)
    assert first.confidence == pytest.approx(0.9) and first.radius_m == 250
    assert first.occurred_at == PUBLISHED
    assert first.half_life_min == HALF_LIFE_MIN
    assert first.urls == ["https://news.example/a/1"] and first.merge_into is None
    # area-level match: lower confidence, wider radius; naive model time is read as UTC
    assert second.confidence == pytest.approx(0.9 * 0.6) and second.radius_m == 2600
    assert second.occurred_at == datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)


def test_geocode_tool_returns_compact_candidates_and_records_them():
    seen = {}

    def final(returns):
        seen["geocode"] = returns[0].content
        return []

    deps = make_deps()
    extracted, _ = llm.run(deps, model=scripted_model([("geocode", {"place_text": "Bethnal Green"})], final))
    assert extracted == []
    place_id = llm.place_id_for("  bethnal   GREEN ")
    assert seen["geocode"] == [
        {"place_id": place_id, "label": "Bethnal Green", "precision_m": 2600, "lat": 51.5303, "lng": -0.0562}
    ]
    assert deps.places == {place_id: PLACES["Bethnal Green"]}


def test_unknown_place_id_is_dropped():
    model = scripted_model(
        [("geocode", {"place_text": "Lloyd Baker Street, Clerkenwell"})],
        lambda returns: [item("p_invented"), item(returns[0].content[0]["place_id"])],
    )
    deps = make_deps()
    extracted, _ = llm.run(deps, model=model)
    events = llm.to_events(extracted, deps, "met_news", "official_statement")
    assert len(extracted) == 2 and len(events) == 1
    assert events[0].external_ref == "met_news:guid-1"


def test_out_of_london_geocode_is_dropped():
    # The tool returns no candidate for a place outside the bbox, so the
    # model has no valid place_id to use
    seen = {}

    def final(returns):
        seen["geocode"] = returns[0].content
        return [item(llm.place_id_for("Paris"), place_text="Paris")]

    deps = make_deps()
    extracted, _ = llm.run(deps, model=scripted_model([("geocode", {"place_text": "Paris"})], final))
    assert seen["geocode"] == [] and deps.places == {}
    assert llm.to_events(extracted, deps, "met_news", "official_statement") == []

    # to_events checks the bbox as well, independently of the tool
    deps.places["p_paris"] = PLACES["Paris"]
    parsed = [llm.ExtractedEvent(**item("p_paris"))]
    assert llm.to_events(parsed, deps, "met_news", "official_statement") == []


def test_future_occurred_at_falls_back_to_published_at():
    deps = make_deps(places={"p_1": PLACES["Bethnal Green"]})
    parsed = [llm.ExtractedEvent(**item("p_1", occurred_at=utcnow() + timedelta(days=2)))]
    assert llm.to_events(parsed, deps, "bbc_london", "news")[0].occurred_at == PUBLISHED


class Lookup:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def find_merge_candidates(self, category, lng, lat, occurred_at, radius_m):
        self.calls.append((category, lng, lat, occurred_at, radius_m))
        return self.events


def existing_event():
    return Event(
        id=uuid4(),
        category=Category.VIOLENT_CRIME,
        title="Stabbing in Clerkenwell",
        geometry={"type": "Point", "coordinates": [-0.1104, 51.5298]},
        lng=-0.1104,
        lat=51.5298,
        radius_m=250,
        severity=0.9,
        confidence=0.7,
        occurred_at=PUBLISHED - timedelta(hours=2),
        source_ids=["bbc_london"],
    )


def test_existing_event_id_is_carried_as_merge_into():
    existing = existing_event()
    lookup = Lookup([existing])
    seen = {}

    def respond(messages, info):
        returns = {r.tool_name: r.content for r in tool_returns(messages)}
        if "geocode" not in returns:
            return ModelResponse(parts=[ToolCallPart("geocode", {"place_text": "Lloyd Baker Street, Clerkenwell"})])
        place_id = returns["geocode"][0]["place_id"]
        if "find_similar_events" not in returns:
            args = {"category": "violent_crime", "place_id": place_id, "occurred_at_iso": "2026-09-18T15:00:00Z"}
            return ModelResponse(parts=[ToolCallPart("find_similar_events", args)])
        seen["similar"] = returns["find_similar_events"]
        found = seen["similar"][0]["event_id"]
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"response": [item(place_id, existing_event_id=found)]})]
        )

    deps = make_deps(lookup=lookup)
    extracted, usage = llm.run(deps, model=FunctionModel(respond))
    events = llm.to_events(extracted, deps, "met_news", "official_statement")

    assert usage.requests == 3 and usage.tool_calls == 2
    assert lookup.calls == [
        ("violent_crime", -0.1104, 51.5289, datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc), 500.0)
    ]
    assert seen["similar"] == [
        {
            "event_id": str(existing.id),
            "title": "Stabbing in Clerkenwell",
            "category": "violent_crime",
            "occurred_at": existing.occurred_at.isoformat(),
            "distance_m": 100,
        }
    ]
    assert events[0].merge_into == existing.id


def test_find_similar_events_without_lookup_and_invalid_existing_id():
    seen = {}

    def respond(messages, info):
        returns = {r.tool_name: r.content for r in tool_returns(messages)}
        if "geocode" not in returns:
            return ModelResponse(parts=[ToolCallPart("geocode", {"place_text": "Bethnal Green"})])
        place_id = returns["geocode"][0]["place_id"]
        if "find_similar_events" not in returns:
            args = {"category": "violent_crime", "place_id": place_id}
            return ModelResponse(parts=[ToolCallPart("find_similar_events", args)])
        seen["similar"] = returns["find_similar_events"]
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"response": [item(place_id, existing_event_id="not-a-uuid")]})]
        )

    deps = make_deps()
    extracted, _ = llm.run(deps, model=FunctionModel(respond))
    assert seen["similar"] == []
    assert llm.to_events(extracted, deps, "met_news", "official_statement")[0].merge_into is None


def test_fetch_article_strips_html_and_is_limited_to_the_article_host():
    page = (
        "<html><head><style>p {color: red}</style><script>var x = 1;</script></head><body>"
        "<nav>Home News Sport</nav><article><h1>Stabbing</h1><p>Police were called to "
        "Lloyd&nbsp;Baker Street &amp; nearby roads.</p></article><footer>Cookies</footer></body></html>"
    )
    fetched = []
    seen = {}

    def fetcher(url):
        fetched.append(url)
        return page + "<p>" + "x" * 10_000 + "</p>"

    def final(returns):
        seen["returns"] = [r.content for r in returns]
        return []

    model = scripted_model(
        [
            ("fetch_article", {"url": "https://www.news.example/a/1"}),
            ("fetch_article", {"url": "https://other.example/a/1"}),
            ("fetch_article", {"url": "file:///etc/passwd"}),
        ],
        final,
    )
    llm.run(make_deps(fetcher=fetcher), model=model)
    own, other, local = seen["returns"]
    assert own == "Stabbing Police were called to Lloyd Baker Street & nearby roads."
    assert other.startswith("error:") and local.startswith("error:")
    assert fetched == ["https://www.news.example/a/1"]
    assert len(llm.html_to_text("<p>" + "x " * 10_000 + "</p>")) == llm.ARTICLE_MAX_CHARS


def test_tool_call_limit_is_enforced():
    calls = []

    def geocoder(place_text):
        calls.append(place_text)
        return None

    def respond(messages, info):
        n = len(tool_returns(messages))
        return ModelResponse(parts=[ToolCallPart("geocode", {"place_text": f"place {n}"})])

    with pytest.raises(UsageLimitExceeded):
        llm.run(make_deps(geocoder=geocoder), model=FunctionModel(respond))
    assert len(calls) <= llm.MAX_TOOL_CALLS

    _, usage, error = llm.run_counted(make_deps(geocoder=geocoder), model=FunctionModel(respond))
    assert isinstance(error, UsageLimitExceeded)
    assert usage.requests == llm.MAX_REQUESTS and usage.tool_calls <= llm.MAX_TOOL_CALLS


def test_agent_runs_with_test_model():
    # TestModel calls every tool once with generated arguments, then returns a
    # generated output whose place_id matches no geocode result
    deps = make_deps(fetcher=lambda url: "<p>text</p>")
    extracted, usage = llm.run(deps, model=TestModel())
    assert usage.tool_calls == 3
    assert llm.to_events(extracted, deps, "met_news", "official_statement") == []


def test_importing_and_building_the_agent_needs_no_model(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert not llm.is_configured()
    assert llm.get_agent() is llm.get_agent()
    monkeypatch.setenv("LLM_MODEL", "anthropic:claude-haiku-4-5")
    assert llm.is_configured() and llm.get_model() == "anthropic:claude-haiku-4-5"


# ---------------------------------------------------------------- extraction entry point


def test_rules_are_used_when_llm_model_is_unset(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(extraction, "geocode", fake_geocoder)
    monkeypatch.setattr(llm, "run_counted", lambda *a, **k: pytest.fail("LLM called while unconfigured"))
    events, llm_calls = extraction.extract_events(article(), "bbc_london", "news")
    assert llm_calls == 0
    assert [e.external_ref for e in events] == ["bbc_london:guid-1"]
    assert events[0].confidence == pytest.approx(0.7) and events[0].severity == 0.95


def test_prefilter_runs_before_the_llm(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(llm, "run_counted", lambda *a, **k: pytest.fail("LLM called for a filtered item"))
    assert extraction.extract_events(article("Man jailed for life following murder"), "met_news", "news") == ([], 0)


def test_llm_path_is_used_when_configured(monkeypatch):
    model = scripted_model(
        [("geocode", {"place_text": "Bethnal Green"})],
        lambda returns: [item(returns[0].content[0]["place_id"], title="Assault in Bethnal Green")],
    )
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(llm, "get_model", lambda: model)
    monkeypatch.setattr(extraction, "geocode", fake_geocoder)
    events, llm_calls = extraction.extract_events(article(), "met_news", "official_statement")
    assert llm_calls == 2
    assert [e.title for e in events] == ["Assault in Bethnal Green"]


def test_rules_are_used_when_the_model_raises(monkeypatch):
    def respond(messages, info):
        raise RuntimeError("provider unavailable")

    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(llm, "get_model", lambda: FunctionModel(respond))
    monkeypatch.setattr(extraction, "geocode", fake_geocoder)
    failures = extraction.llm_failures
    events, _ = extraction.extract_events(article(), "met_news", "official_statement")
    assert extraction.llm_failures == failures + 1
    # title and severity of the rule-based extractor
    assert events[0].title == article().title and events[0].severity == 0.95


# ---------------------------------------------------------------- RSS source and pipeline

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Feed</title>
<item>
  <title>Man dies following stabbing in Lloyd Baker Street, Clerkenwell</title>
  <description>&lt;p&gt;Detectives are appealing for witnesses &amp;amp; footage.&lt;/p&gt;</description>
  <link>https://news.example/a/1</link>
  <guid isPermaLink="false">item-1</guid>
  <pubDate>Fri, 18 Sep 2026 17:37:00 +0100</pubDate>
</item>
<item>
  <title>Council approves new cycle lanes</title>
  <link>https://news.example/a/2</link>
</item>
</channel></rss>"""


def test_rss_parsing():
    first, second = parse_feed(RSS, "bbc_london")
    assert first.source_id == "bbc_london" and first.external_id == "item-1"
    assert first.payload == {
        "title": "Man dies following stabbing in Lloyd Baker Street, Clerkenwell",
        "description": "Detectives are appealing for witnesses & footage.",
        "link": "https://news.example/a/1",
        "guid": "item-1",
        "pub_date": "Fri, 18 Sep 2026 17:37:00 +0100",
    }
    # no guid: the link identifies the item; no pubDate: published_at is None
    assert second.external_id == "https://news.example/a/2"
    art = RssNewsSource("x", "https://news.example/rss", "news").article(second)
    assert art.published_at is None and art.description == ""
    assert RssNewsSource("x", "u", "news").article(first).published_at == PUBLISHED


def test_bbc_london_source_is_registered():
    from backend.pipeline import SOURCES

    src = SOURCES["bbc_london"]
    assert isinstance(src, BbcLondonSource) and src.source_type == "news" and not src.snapshot
    assert src.url == "https://feeds.bbci.co.uk/news/england/london/rss.xml"
    assert SOURCES["met_news"].source_type == "official_statement"


class FeedSource(RssNewsSource):
    def fetch(self, cursor):
        return parse_feed(RSS, self.id), cursor


def test_pipeline_rules_path_keeps_counts_unchanged(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(extraction, "geocode", fake_geocoder)
    repo = MemoryRepo()
    assert run_poll(FeedSource("bbc_london", "u", "news"), repo) == {"fetched": 2, "inserted": 1, "ended": 0}
    assert repo.events["bbc_london:item-1"].raw_item_ids == [1]


def test_pipeline_multi_event_items_llm_calls_and_lookup(monkeypatch):
    def final(returns):
        ids = [r.content[0]["place_id"] for r in returns]
        return [item(ids[0]), item(ids[1], title="Second incident", place_text="Bethnal Green")]

    model = scripted_model(
        [("geocode", {"place_text": "Lloyd Baker Street, Clerkenwell"}), ("geocode", {"place_text": "Bethnal Green"})],
        final,
    )
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(llm, "get_model", lambda: model)
    monkeypatch.setattr(extraction, "geocode", fake_geocoder)

    lookups = []
    original = extraction.extract_events

    def spy(article, source_id, source_type, lookup=None, rules_fallback=True):
        lookups.append(lookup)
        return original(article, source_id, source_type, lookup, rules_fallback)

    monkeypatch.setattr("backend.sources.rss.extract_events", spy)

    class RepoWithLookup(MemoryRepo):
        def find_merge_candidates(self, category, lng, lat, occurred_at, radius_m):
            return []

    repo = RepoWithLookup()
    counts = run_poll(FeedSource("bbc_london", "u", "news"), repo)
    assert counts == {"fetched": 2, "inserted": 2, "ended": 0, "llm_calls": 2}
    assert set(repo.events) == {"bbc_london:item-1", "bbc_london:item-1#2"}
    assert all(ev.raw_item_ids == [1] for ev in repo.events.values())
    # the second feed item fails the pre-filter, so no extraction is started for it
    assert lookups == [repo]

    # a repo without find_merge_candidates is not passed as the lookup
    lookups.clear()
    run_poll(FeedSource("bbc_london", "u", "news"), MemoryRepo())
    assert lookups == [None]
