"""Storage-level merge of events from unstructured sources (SqliteRepo.upsert_events)."""

from datetime import timedelta
from uuid import UUID

import pytest

from backend import db
from backend.db import SqliteRepo
from backend.models import Category, Event, RawItem
from backend.pipeline import run_poll
from backend.scoring import distance_m
from backend.tests.test_scoring import SOHO, T0, make_event

LNG_80M = 80 / 69_300  # degrees of longitude for about 80 m at London's latitude


@pytest.fixture
def repo(tmp_path):
    db.connect(tmp_path / "risk.sqlite")
    return SqliteRepo()


def rows() -> list[Event]:
    return db.events_geojson(T0 + timedelta(days=1))


def bbc(**kw) -> Event:
    defaults = dict(
        id=None,
        external_ref="bbc_london:a1",
        title="Man stabbed in Soho",
        urls=["https://bbc.example/a1"],
        raw_item_ids=[1],
    )
    return make_event(**(defaults | kw))


def met(**kw) -> Event:
    defaults = dict(
        id=None,
        external_ref="met_news:m1",
        title="Appeal after stabbing in Soho",
        lng=SOHO[0] + LNG_80M,
        radius_m=100,
        severity=0.9,
        confidence=0.9,
        source_confidence={"met_news": 0.9},
        source_ids=["met_news"],
        urls=["https://met.example/m1"],
        raw_item_ids=[2],
        occurred_at=T0 + timedelta(minutes=40),
    )
    return make_event(**(defaults | kw))


def tfl(**kw) -> Event:
    defaults = dict(
        id=None,
        external_ref="tfl_road:T1",
        category=Category.ROAD_CLOSURE,
        title="TfL: road closed",
        radius_m=150,
        severity=0.55,
        confidence=0.95,
        source_confidence={"tfl_road": 0.95},
        source_ids=["tfl_road"],
        half_life_min=None,
        mergeable=False,
    )
    return make_event(**(defaults | kw))


def test_two_news_sources_become_one_event(repo):
    assert repo.upsert_events([bbc()]) == {"inserted": 1, "merged": 0}
    assert repo.upsert_events([met()]) == {"inserted": 0, "merged": 1}

    (ev,) = rows()
    assert 70 < distance_m(SOHO[0], SOHO[1], met().lng, met().lat) < 90
    assert ev.external_ref == "bbc_london:a1"
    assert ev.title == "Man stabbed in Soho"
    assert ev.source_ids == ["bbc_london", "met_news"]
    assert ev.confidence == pytest.approx(1 - (1 - 0.5) * (1 - 0.9))
    assert ev.urls == ["https://bbc.example/a1", "https://met.example/m1"]
    assert ev.raw_item_ids == [1, 2]
    assert ev.severity == 0.9
    # the more precise geometry (smaller radius) is kept
    assert ev.radius_m == 100 and ev.lng == met().lng
    assert ev.geometry["coordinates"] == [met().lng, met().lat]
    assert ev.occurred_at == T0


def test_repolling_either_source_changes_nothing(repo):
    repo.upsert_events([bbc()])
    repo.upsert_events([met()])
    with db._tx() as conn:
        before = [tuple(r) for r in conn.execute("select * from events")]

    for _ in range(2):
        assert repo.upsert_events([bbc()]) == {"inserted": 0, "merged": 0}
        assert repo.upsert_events([met()]) == {"inserted": 0, "merged": 0}
        assert repo.upsert_structured_events([bbc(), met()]) == 0
    with db._tx() as conn:
        assert [tuple(r) for r in conn.execute("select * from events")] == before
        assert conn.execute("select count(*) from event_refs").fetchone()[0] == 2


def test_refresh_of_merged_row_only_applies_safe_fields(repo):
    repo.upsert_events([bbc(summary=None)])
    repo.upsert_events([met(summary=None)])
    # the less precise, lower-severity report changes; it fills the summary and adds a url
    repo.upsert_events([bbc(summary="Police were called.", urls=["https://bbc.example/a1", "https://bbc.example/live"], lng=SOHO[0] - 0.001)])
    (ev,) = rows()
    assert ev.summary == "Police were called."
    assert ev.urls == ["https://bbc.example/a1", "https://met.example/m1", "https://bbc.example/live"]
    assert ev.radius_m == 100 and ev.lng == met().lng
    assert ev.severity == 0.9
    assert ev.confidence == pytest.approx(0.95)


def test_unmerged_news_event_updates_as_before(repo):
    repo.upsert_events([bbc()])
    repo.upsert_events([bbc(title="Man stabbed in Soho: arrest made", severity=0.6)])
    (ev,) = rows()
    assert ev.title == "Man stabbed in Soho: arrest made"
    assert ev.severity == 0.6 and ev.confidence == 0.5


@pytest.mark.parametrize(
    "other",
    [
        met(category=Category.FIRE),
        met(occurred_at=T0 + timedelta(hours=3, minutes=1)),
        met(lng=SOHO[0] + 0.02),  # ~1.4 km
    ],
    ids=["other-group", "outside-window", "too-far"],
)
def test_non_matching_reports_stay_separate(repo, other):
    repo.upsert_events([bbc()])
    assert repo.upsert_events([other]) == {"inserted": 1, "merged": 0}
    assert len(rows()) == 2


def test_structured_events_never_merge(repo):
    a = tfl()
    b = tfl(external_ref="tfl_road:T2")
    c = tfl(external_ref="tfl_transit:X", source_ids=["tfl_transit"], source_confidence={"tfl_transit": 0.95})
    assert repo.upsert_events([a, b, c]) == {"inserted": 3, "merged": 0}
    assert len(rows()) == 3


def test_news_event_merges_into_structured_event_of_same_group(repo):
    repo.upsert_events([tfl()])
    news = met(category=Category.ROAD_CLOSURE, severity=0.4, radius_m=250)
    assert repo.upsert_events([news]) == {"inserted": 0, "merged": 1}
    (ev,) = rows()
    assert ev.external_ref == "tfl_road:T1"
    assert ev.source_ids == ["tfl_road", "met_news"]
    assert ev.radius_m == 150 and ev.severity == 0.55 and ev.half_life_min is None
    assert ev.confidence == pytest.approx(1 - 0.05 * 0.1)

    # the row is still owned by the snapshot feed: it ends when TfL drops it,
    # and the feed reappearing clears ended_at
    assert repo.end_missing("tfl_road", [], T0 + timedelta(hours=1)) == 1
    repo.upsert_events([news])
    assert rows()[0].ended_at is not None
    repo.upsert_events([tfl()])
    assert rows()[0].ended_at is None

    # a crime report at the same place is a different group and stays separate
    assert repo.upsert_events([met(external_ref="met_news:m2")]) == {"inserted": 1, "merged": 0}


def test_structured_event_arriving_after_news_gets_its_own_row(repo):
    repo.upsert_events([met(category=Category.ROAD_CLOSURE)])
    assert repo.upsert_events([tfl()]) == {"inserted": 1, "merged": 0}
    assert repo.end_missing("tfl_road", [], T0 + timedelta(hours=1)) == 1
    assert {e.external_ref: e.ended_at is not None for e in rows()} == {
        "met_news:m1": False,
        "tfl_road:T1": True,
    }


def test_closest_candidate_is_chosen(repo):
    repo.upsert_events([bbc(mergeable=False), bbc(external_ref="bbc_london:a2", lng=SOHO[0] + 2 * LNG_80M, mergeable=False)])
    assert len(rows()) == 2
    repo.upsert_events([met(lng=SOHO[0] + 1.5 * LNG_80M)])
    merged = [e for e in rows() if len(e.source_ids) == 2]
    assert [e.external_ref for e in merged] == ["bbc_london:a2"]


def test_merge_into_is_honoured(repo):
    repo.upsert_events([bbc()])
    target_id = rows()[0].id
    if "merge_into" not in Event.model_fields:
        pytest.skip("Event.merge_into is added by the extraction track")
    far = met(lng=SOHO[0] + 0.05, mergeable=False, merge_into=target_id)
    assert repo.upsert_events([far]) == {"inserted": 0, "merged": 1}
    # a target in another category group is ignored
    fire = met(external_ref="met_news:m3", category=Category.FIRE, lng=SOHO[0] + 0.05, merge_into=target_id)
    assert repo.upsert_events([fire]) == {"inserted": 1, "merged": 0}


def test_merge_into_without_model_field(repo):
    """The storage layer reads merge_into with getattr, so it works before the
    field exists on Event."""

    class EventWithTarget(Event):
        merge_into: UUID | None = None

    repo.upsert_events([bbc()])
    target_id = rows()[0].id
    data = met(lng=SOHO[0] + 0.05, mergeable=False).model_dump() | {"merge_into": target_id}
    assert repo.upsert_events([EventWithTarget(**data)]) == {"inserted": 0, "merged": 1}
    (ev,) = rows()
    assert ev.id == target_id and ev.source_ids == ["bbc_london", "met_news"]

    missing = data | {"external_ref": "met_news:m4", "merge_into": UUID(int=1)}
    assert repo.upsert_events([EventWithTarget(**missing)]) == {"inserted": 1, "merged": 0}


def test_find_merge_candidates(repo):
    repo.upsert_events([
        bbc(mergeable=False),
        bbc(external_ref="bbc_london:near", lng=SOHO[0] + LNG_80M, mergeable=False),
        bbc(external_ref="bbc_london:wide", lng=SOHO[0] + 0.01, radius_m=1000, mergeable=False),
        bbc(external_ref="bbc_london:far", lng=SOHO[0] + 0.02, mergeable=False),
        bbc(external_ref="bbc_london:fire", category=Category.FIRE, mergeable=False),
        bbc(external_ref="bbc_london:old", occurred_at=T0 - timedelta(hours=4), mergeable=False),
        bbc(external_ref="bbc_london:ended", ended_at=T0 - timedelta(hours=3, minutes=1),
            occurred_at=T0 - timedelta(hours=2), mergeable=False),
    ])
    found = repo.find_merge_candidates("property_crime", SOHO[0], SOHO[1], T0, 100.0)
    assert [e.external_ref for e in found] == ["bbc_london:a1", "bbc_london:near", "bbc_london:wide"]
    assert repo.find_merge_candidates("flood", SOHO[0], SOHO[1], T0, 100.0) == []


def test_poll_records_merged_count(repo):
    class News:
        id = "met_news"
        snapshot = False

        def fetch(self, cursor):
            return [RawItem(source_id=self.id, external_id="m1", payload={})], cursor

        def to_event(self, raw):
            return met()

    repo.upsert_events([bbc()])
    assert run_poll(News(), repo) == {"fetched": 1, "inserted": 0, "ended": 0, "merged": 1}
    assert run_poll(News(), repo) == {"fetched": 1, "inserted": 0, "ended": 0}
    with db._tx() as conn:
        assert [r["merged"] for r in conn.execute("select merged from agent_runs order by id")] == [1, 0]


def test_event_refs_backfilled_on_old_database(tmp_path):
    # A database file from before event_refs existed: events present, table absent.
    path = tmp_path / "old.sqlite"
    db.connect(path)
    SqliteRepo().upsert_events([bbc(), tfl()])
    with db._tx() as conn:
        conn.execute("drop table event_refs")
    ids = {e.external_ref: e.id for e in rows()}

    db.connect(path)
    with db._tx() as conn:
        mapped = {r["external_ref"]: UUID(r["event_id"]) for r in conn.execute("select * from event_refs")}
    assert mapped == ids
    assert SqliteRepo().upsert_events([bbc(), tfl()]) == {"inserted": 0, "merged": 0}
    assert len(rows()) == 2


def test_mergeable_flag_is_not_serialised():
    assert met().mergeable and "mergeable" not in met().model_dump()
    assert tfl().mergeable is False


def test_reextracted_report_with_corrected_time_merges_into_the_other_event(tmp_path):
    """A report first stored with its publication time, then re-extracted with the
    real incident time, joins the event that other sources already describe."""
    from datetime import datetime, timedelta, timezone

    from backend import db
    from backend.db import SqliteRepo
    from backend.models import Category, Event

    db.connect(tmp_path / "risk.sqlite")
    repo = SqliteRepo()
    incident = datetime(2026, 9, 16, 22, 20, tzinfo=timezone.utc)
    published = incident + timedelta(hours=42)

    def report(source: str, occurred_at: datetime, confidence: float) -> Event:
        return Event(
            external_ref=f"{source}:guid",
            category=Category.VIOLENT_CRIME,
            title="Fatal stabbing on Lloyd Baker Street",
            geometry={"type": "Point", "coordinates": [-0.1112, 51.5286]},
            lng=-0.1112,
            lat=51.5286,
            radius_m=250,
            severity=0.95,
            confidence=confidence,
            source_confidence={source: confidence},
            half_life_min=1440,
            occurred_at=occurred_at,
            source_ids=[source],
            urls=[f"https://example.org/{source}"],
            mergeable=True,
        )

    assert repo.upsert_events([report("met_news", published, 0.9)]) == {"inserted": 1, "merged": 0}
    assert repo.upsert_events([report("bbc_london", incident, 0.7)]) == {"inserted": 1, "merged": 0}

    # the Met report is extracted again, now with the real incident time
    assert repo.upsert_events([report("met_news", incident, 0.9)]) == {"inserted": 0, "merged": 1}
    events = db.events_geojson(datetime(2026, 9, 19, tzinfo=timezone.utc))
    assert len(events) == 1
    assert sorted(events[0].source_ids) == ["bbc_london", "met_news"]
    assert events[0].confidence == pytest.approx(1 - 0.1 * 0.3)

    # polling either source again changes nothing
    assert repo.upsert_events([report("met_news", incident, 0.9), report("bbc_london", incident, 0.7)]) == {
        "inserted": 0,
        "merged": 0,
    }
    assert len(db.events_geojson(datetime(2026, 9, 19, tzinfo=timezone.utc))) == 1
