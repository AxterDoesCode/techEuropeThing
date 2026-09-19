"""Tests of the TfL road source allow-list.

fixtures/tfl_road.json holds the kept items. They are real feed items captured on
2026-09-19 (several with status "Recently Cleared", fetched by id). Their
endDateTime is replaced by 2036-01-01 so that the events do not expire while the
tests run. Exceptions:
- TIMS-206772 is SYNTHETIC: the geometry, streets and times of a real roadworks
  item, relabelled as a collision. Other test modules refer to this id.
- TEST-NO-IMPACT is synthetic and is a roadworks item, which is dropped.

fixtures/tfl_road_dropped.json holds one real item for every dropped
(category, subCategory) pair observed upstream, with the bulky geometry fields
removed.
"""

import json
from pathlib import Path

import pytest

from backend.models import Category, RawItem
from backend.pipeline import run_poll
from backend.sources.tfl_road import KEPT_CATEGORIES, KEPT_PAIRS, TflRoadSource, classify
from backend.tests.test_pipeline import FIXTURE, FixtureSource, MemoryRepo

DROPPED = json.loads((Path(__file__).parent / "fixtures" / "tfl_road_dropped.json").read_text())
KEPT_ITEMS = [d for d in FIXTURE if d["id"] != "TEST-NO-IMPACT"]


def _event(d: dict):
    return TflRoadSource().to_event(RawItem(source_id="tfl_road", external_id=d["id"], payload=d))


def test_fixtures_cover_every_kept_rule_and_no_dropped_item_is_kept():
    pairs = {(d["category"], d["subCategory"]) for d in KEPT_ITEMS}
    assert pairs >= set(KEPT_PAIRS)
    assert {c for c, _ in pairs} == set(KEPT_CATEGORIES) | {"Planned events"}
    assert {d["category"] for d in DROPPED} == {"Works", "Asset issues", "Network delays", "Breakdowns", "Planned events"}
    assert all(classify(d) is None for d in DROPPED)


@pytest.mark.parametrize("item", DROPPED, ids=lambda d: f"{d['category']}/{d['subCategory']}")
def test_dropped_pairs_produce_no_event(item):
    assert _event(item) is None


@pytest.mark.parametrize("item", KEPT_ITEMS, ids=lambda d: f"{d['id']}-{d['subCategory']}")
def test_kept_items_produce_one_event(item):
    event = _event(item)
    assert event.title.startswith(item["subCategory"] + ": ")
    assert event.half_life_min is None
    assert event.external_ref == f"tfl_road:{item['id']}"


def test_category_and_severity_by_incident_type():
    by_sub = {d["subCategory"]: (_event(d).category, _event(d).severity) for d in KEPT_ITEMS}
    assert by_sub == {
        "Vehicle collision": (Category.ROAD_CLOSURE, 0.4),
        "Police activity": (Category.ROAD_CLOSURE, 0.4),
        "Safety related incident": (Category.ROAD_CLOSURE, 0.4),
        "Fire": (Category.FIRE, 0.6),
        "Flooding": (Category.FLOOD, 0.4),
        "Demonstration/March": (Category.DISORDER, 0.35),
    }


def test_upstream_traffic_severity_is_ignored():
    police = next(d for d in KEPT_ITEMS if d["subCategory"] == "Police activity")
    assert police["severity"] == "No impact"
    assert _event(police).severity == 0.4
    assert _event({**police, "severity": "Severe"}).severity == 0.4


@pytest.mark.parametrize(
    "category, sub, expected",
    [
        # SYNTHETIC subCategory values: their upstream spelling is not known
        ("Hazards", "Gas leak", (Category.ROAD_CLOSURE, 0.4)),
        ("Emergency service incidents", "Future upstream value", (Category.ROAD_CLOSURE, 0.4)),
        ("Collisions", None, (Category.ROAD_CLOSURE, 0.4)),
        ("Weather", "Ice", (Category.ROAD_CLOSURE, 0.4)),
        ("Future upstream category", "Vehicle collision", None),
        ("Planned events", "Fire", None),
        ("Planned events", None, None),
        (None, None, None),
    ],
)
def test_unknown_subcategory_is_kept_and_unknown_category_is_dropped(category, sub, expected):
    event = _event({**KEPT_ITEMS[1], "category": category, "subCategory": sub})
    assert (event and (event.category, event.severity)) == expected


def test_poll_ends_stored_events_of_pairs_that_are_now_dropped():
    """State after deploy: the database holds open events that the previous
    version created from roadworks and other dropped items, and the feed still
    lists those items."""

    class PreviousVersion(FixtureSource):
        def to_event(self, raw):
            relabelled = {**raw.payload, "category": "Collisions", "subCategory": "Vehicle collision"}
            return super().to_event(raw.model_copy(update={"payload": relabelled}))

    feed = FIXTURE + DROPPED
    repo = MemoryRepo()
    assert run_poll(PreviousVersion(feed), repo)["inserted"] == len(feed)

    counts = run_poll(FixtureSource(feed), repo)
    stale = {f"tfl_road:{d['id']}" for d in DROPPED} | {"tfl_road:TEST-NO-IMPACT"}
    assert counts == {"fetched": len(feed), "inserted": 0, "ended": len(stale)}
    assert {ref for ref, ev in repo.events.items() if ev.ended_at is not None} == stale

    # idempotent
    assert run_poll(FixtureSource(feed), repo) == {"fetched": len(feed), "inserted": 0, "ended": 0}

    # A poll in which no item is kept still ends the remaining events, because the
    # fetch result itself is not empty.
    assert run_poll(FixtureSource(DROPPED), repo)["ended"] == len(KEPT_ITEMS)
    assert all(ev.ended_at is not None for ev in repo.events.values())
