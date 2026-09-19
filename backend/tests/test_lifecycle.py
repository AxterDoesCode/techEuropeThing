"""Event lifecycle across storage, extraction and the API: confirmation on merge,
ending by a report, the ingestion gate, the schema migration and the event listing."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import db, llm
from backend.db import SqliteRepo
from backend.geocode import GeoResult
from backend.located import Article
from backend.models import Category, utcnow
from backend.scoring import event_risk, event_state
from backend.tests.conftest import FROZEN_NOW
from backend.tests.test_scoring import T0, make_event


@pytest.fixture
def repo(tmp_path):
    db.connect(tmp_path / "risk.sqlite")
    return SqliteRepo()


def stored():
    return db.events_geojson(T0 + timedelta(days=30))


def protest(**kw):
    defaults = dict(
        id=None,
        external_ref="bbc_london:p1",
        category=Category.DISORDER,
        subtype="tense_protest",
        title="Protest in Soho",
        occurred_at=T0 - timedelta(days=2),
        is_ongoing=True,
        last_confirmed_at=T0 - timedelta(days=2),
    )
    return make_event(**(defaults | kw))


# ---------------------------------------------------------------- storage


def test_lifecycle_fields_round_trip_and_insert_sets_last_confirmed_at(repo):
    repo.upsert_events([protest(), make_event(id=None, external_ref="bbc_london:s1", last_confirmed_at=None)])
    by_ref = {e.external_ref: e for e in stored()}
    p = by_ref["bbc_london:p1"]
    assert (p.subtype, p.is_ongoing, p.feed_managed) == ("tense_protest", True, False)
    assert p.last_confirmed_at == T0 - timedelta(days=2)
    s = by_ref["bbc_london:s1"]
    assert not s.is_ongoing and s.subtype is None
    assert utcnow() - s.last_confirmed_at < timedelta(minutes=1)


def test_merge_refreshes_last_confirmed_at_and_keeps_a_two_day_old_protest_at_full_risk(repo):
    repo.upsert_events([protest()])
    (before,) = stored()
    # not confirmed for two days: ended at the last confirmation, past the hard cap
    assert event_state(before, T0) == "ended" and event_risk(before, T0) == 0.0

    # the first report is seen again and still says the protest is in progress
    repo.upsert_events([protest(last_confirmed_at=T0 - timedelta(hours=5))])
    # A second source reports on it two days after the start. Its time is outside
    # MERGE_WINDOW of the start; it matches because the protest was in progress then.
    report = protest(
        external_ref="met_news:p9",
        title="Officers remain at protest in Soho",
        occurred_at=T0 - timedelta(hours=1),
        last_confirmed_at=T0 - timedelta(hours=1),
        source_ids=["met_news"],
        source_confidence={"met_news": 0.9},
    )
    assert repo.upsert_events([report]) == {"inserted": 0, "merged": 1}
    (ev,) = stored()
    assert ev.occurred_at == T0 - timedelta(days=2)
    assert ev.last_confirmed_at == T0 - timedelta(hours=1) and ev.is_ongoing
    assert event_state(ev, T0) == "ongoing"
    assert event_risk(ev, T0) == pytest.approx(ev.severity * ev.confidence)
    # 12 h after the last confirmation it is still ongoing; after that it has ended
    assert event_state(ev, T0 + timedelta(hours=11)) == "ongoing"
    assert event_state(ev, T0 + timedelta(hours=11, minutes=1)) == "ended"


def test_same_report_seen_again_confirms_only_when_it_says_ongoing(repo):
    repo.upsert_events([protest(last_confirmed_at=T0 - timedelta(hours=5))])
    repo.upsert_events([protest(last_confirmed_at=T0 - timedelta(hours=1))])
    (ev,) = stored()
    assert ev.last_confirmed_at == T0 - timedelta(hours=1)
    # the edited report no longer says the protest is in progress
    repo.upsert_events([protest(is_ongoing=False, last_confirmed_at=T0)])
    (ev,) = stored()
    assert ev.ended_at == T0 and ev.last_confirmed_at == T0 - timedelta(hours=1)
    assert event_state(ev, T0 - timedelta(minutes=1)) == "ongoing" and event_state(ev, T0) == "ended"


def test_one_off_incident_does_not_merge_with_a_report_days_later(repo):
    repo.upsert_events([make_event(id=None, external_ref="bbc_london:s1", occurred_at=T0 - timedelta(days=2))])
    later = make_event(id=None, external_ref="met_news:s2", source_ids=["met_news"])
    assert repo.upsert_events([later]) == {"inserted": 1, "merged": 0}


def test_false_alarm_ends_the_existing_event_and_never_creates_one(repo):
    alarm = dict(external_ref="met_news:fa", source_ids=["met_news"], resolution="false_alarm", is_ongoing=False)
    assert repo.upsert_events([protest(**alarm)]) == {"inserted": 0, "merged": 0}
    assert stored() == []

    repo.upsert_events([protest(last_confirmed_at=T0 - timedelta(hours=2))])
    report = protest(**alarm, occurred_at=T0 - timedelta(hours=1), last_confirmed_at=T0 - timedelta(hours=1))
    assert repo.upsert_events([report]) == {"inserted": 0, "merged": 1}
    (ev,) = stored()
    assert ev.ended_at == T0 - timedelta(hours=1) and ev.severity == 0
    assert event_risk(ev, T0) == 0.0


def test_resolved_report_ends_the_event_and_keeps_the_residual(repo):
    repo.upsert_events([protest(last_confirmed_at=T0 - timedelta(hours=2))])
    report = protest(
        external_ref="met_news:r1", source_ids=["met_news"], resolution="resolved", is_ongoing=False,
        severity=0.3, occurred_at=T0 - timedelta(hours=1), last_confirmed_at=T0 - timedelta(hours=1),
    )
    repo.upsert_events([report])
    (ev,) = stored()
    assert ev.ended_at == T0 - timedelta(hours=1) and ev.severity == 0.8
    # disorder half-life 2 h: one hour after the end
    assert event_risk(ev, T0) == pytest.approx(0.8 * ev.confidence * 0.5 ** 0.5)
    # a later report that does not say "in progress" does not reopen it
    repo.upsert_events([protest(external_ref="bbc_london:p2", is_ongoing=False, occurred_at=T0)])
    assert {e.ended_at for e in stored()} >= {T0 - timedelta(hours=1)}


def test_feed_event_ended_by_its_feed_is_not_reopened_or_ended_by_reports(repo):
    feed = make_event(
        id=None, external_ref="tfl_road:T1", category=Category.ROAD_CLOSURE, half_life_min=None,
        mergeable=False, source_ids=["tfl_road"],
    )
    repo.upsert_events([feed])
    news = make_event(
        id=None, external_ref="bbc_london:c1", category=Category.ROAD_CLOSURE, resolution="resolved",
        occurred_at=T0 + timedelta(hours=1),
    )
    assert repo.upsert_events([news])["merged"] == 1
    (ev,) = stored()
    assert ev.feed_managed and ev.ended_at is None
    assert repo.end_missing("tfl_road", [], T0 + timedelta(hours=2)) == 1
    (ev,) = stored()
    assert ev.ended_at == T0 + timedelta(hours=2) and not ev.is_ongoing


def test_scoring_candidates_excludes_events_without_risk(repo):
    now = utcnow()
    repo.upsert_events(
        [
            make_event(id=None, external_ref="a:fresh", occurred_at=now - timedelta(hours=1)),
            make_event(id=None, external_ref="a:capped", lng=0.1, occurred_at=now - timedelta(hours=37)),
            make_event(id=None, external_ref="a:old", lng=0.2, occurred_at=now - timedelta(days=30)),
            protest(external_ref="a:protest", lng=0.3, occurred_at=now - timedelta(days=20),
                    last_confirmed_at=now - timedelta(hours=1)),
            make_event(id=None, external_ref="tfl_road:T1", category=Category.ROAD_CLOSURE, lng=0.25,
                       half_life_min=None, mergeable=False, occurred_at=now - timedelta(days=40)),
        ]
    )
    refs = {e.external_ref for e in repo.scoring_candidates(now)}
    assert refs == {"a:fresh", "a:protest", "tfl_road:T1"}


# ---------------------------------------------------------------- migration

OLD_EVENTS_TABLE = """
create table events (
  id text primary key, external_ref text unique, category text not null, title text not null,
  summary text, geometry text not null, lng real not null, lat real not null, radius_m real not null,
  h3_r10 text not null, h3_r9 text not null, h3_r7 text not null,
  severity real not null, confidence real not null, source_confidence text not null default '{}',
  half_life_min real, occurred_at text not null, expires_at text, ended_at text,
  source_ids text not null, raw_item_ids text not null default '[]', urls text not null default '[]',
  created_at text not null, updated_at text not null
);
"""


def test_old_database_gets_the_lifecycle_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.executescript(OLD_EVENTS_TABLE)
    updated = "2026-09-19T11:00:00.000000+00:00"
    base = ("{\"type\":\"Point\",\"coordinates\":[-0.13,51.51]}", -0.13, 51.51, 200, "a", "b", "c", 0.5, 0.9,
            "2026-09-19T10:00:00.000000+00:00")
    rows = [
        ("00000000-0000-0000-0000-000000000001", "tfl_road:live", "road_closure", None, None),
        ("00000000-0000-0000-0000-000000000002", "tfl_road:gone", "road_closure", None, "2026-09-19T10:30:00.000000+00:00"),
        ("00000000-0000-0000-0000-000000000003", "met_news:m1", "violent_crime", 1440, None),
    ]
    for id_, ref, category, half_life, ended_at in rows:
        old.execute(
            "insert into events (id, external_ref, category, title, geometry, lng, lat, radius_m, h3_r10, h3_r9,"
            " h3_r7, severity, confidence, occurred_at, half_life_min, ended_at, source_ids, created_at, updated_at)"
            " values (?, ?, ?, 't', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[\"s\"]', ?, ?)",
            [id_, ref, category, *base, half_life, ended_at, updated, updated],
        )
    old.commit()
    old.close()

    db.connect(path)
    db.connect(path)  # running the migration again changes nothing
    confirmed = datetime(2026, 9, 19, 11, 0, tzinfo=timezone.utc)
    by_ref = {e.external_ref: e for e in db.events_geojson(confirmed + timedelta(days=1))}
    live, gone, news = by_ref["tfl_road:live"], by_ref["tfl_road:gone"], by_ref["met_news:m1"]
    assert (live.feed_managed, live.is_ongoing) == (True, True)
    assert (gone.feed_managed, gone.is_ongoing) == (True, False)
    assert (news.feed_managed, news.is_ongoing, news.subtype) == (False, False, None)
    assert {e.last_confirmed_at for e in by_ref.values()} == {confirmed}
    assert event_state(live, confirmed) == "ongoing" and event_state(news, confirmed) == "ended"

    # new rows can be written to the migrated file, which still has half_life_min
    assert SqliteRepo().upsert_events([protest()]) == {"inserted": 1, "merged": 0}
    assert db.changes_since(confirmed)["events"][0].external_ref == "bbc_london:p1"


# ---------------------------------------------------------------- ingestion gate

PUBLISHED = datetime(2026, 9, 18, 9, 0, tzinfo=timezone.utc)
BETHNAL_GREEN = GeoResult(-0.0562, 51.5303, 2600, "Bethnal Green", "test")


def appeal_events(**fields):
    art = Article(
        title="Detectives appeal for information after rape in Bethnal Green",
        description="",
        url="https://news.met.police.uk/news/appeal",
        published_at=PUBLISHED,
        source_id="met_news",
        guid="appeal-1",
    )
    deps = llm.ExtractionDeps(article=art, places={"p_1": BETHNAL_GREEN})
    item = llm.ExtractedEvent(
        category="violent_crime",
        subtype="sexual_assault",
        title="Rape in Bethnal Green",
        summary="Detectives are appealing for information.",
        place_id="p_1",
        place_text="Bethnal Green",
        severity=0.8,
        **fields,
    )
    return llm.to_events([item], deps, "met_news", "official_statement")


def test_appeal_about_an_old_offence_creates_no_event():
    # the body says the offence happened on 1 August 2024; published 18 Sep 2026
    assert appeal_events(occurred_at="2024-08-01T00:00:00", is_recent=False,
                         recency_reason="happened on 1 August 2024") == []
    # no incident time and no indication that it is recent: not dated with the publication time
    assert appeal_events(occurred_at=None, is_recent=False) == []
    # exactly past the hard cap of 72 h for a sexual assault
    assert appeal_events(occurred_at=FROZEN_NOW - timedelta(hours=72, minutes=1), is_recent=False) == []


def test_recent_incident_passes_the_gate():
    (ev,) = appeal_events(occurred_at=None, is_recent=True, recency_reason="in the early hours of this morning")
    assert ev.occurred_at == PUBLISHED and ev.last_confirmed_at == PUBLISHED
    assert ev.subtype == "sexual_assault" and ev.radius_m == 800 and not ev.is_ongoing
    (ev,) = appeal_events(occurred_at="2026-09-17T23:30:00", is_recent=False)
    assert ev.occurred_at == datetime(2026, 9, 17, 23, 30, tzinfo=timezone.utc)
    assert ev.last_confirmed_at == PUBLISHED


def test_is_recent_has_no_default():
    with pytest.raises(ValueError):
        appeal_events()


def test_ongoing_event_passes_the_gate_whatever_its_start_and_modifiers_apply():
    (ev,) = appeal_events(occurred_at="2026-09-10T12:00:00", is_recent=True, is_ongoing=True, suspect_at_large=True)
    assert ev.is_ongoing and ev.severity == pytest.approx(0.85)
    (ev,) = appeal_events(occurred_at=None, is_recent=True, is_ongoing=True, resolved=True)
    assert not ev.is_ongoing and ev.resolution == "resolved" and ev.severity == pytest.approx(0.48)


def test_false_alarm_needs_an_existing_event():
    assert appeal_events(is_recent=True, false_alarm=True) == []
    target = "00000000-0000-0000-0000-0000000000aa"
    (ev,) = appeal_events(is_recent=True, false_alarm=True, existing_event_id=target)
    assert ev.resolution == "false_alarm" and str(ev.merge_into) == target


# ---------------------------------------------------------------- API


def test_event_listing_states(repo):
    from backend.api import web

    now = utcnow()
    repo.upsert_events(
        [
            protest(external_ref="x:ongoing", occurred_at=now - timedelta(days=2),
                    last_confirmed_at=now - timedelta(hours=1)),
            make_event(id=None, external_ref="x:residual", lng=0.1, subtype="stabbing",
                       occurred_at=now - timedelta(hours=18)),
            make_event(id=None, external_ref="x:capped", lng=0.2, subtype="stabbing",
                       occurred_at=now - timedelta(hours=73)),
        ]
    )
    client = TestClient(web)
    listed = {f["properties"]["external_ref"]: f["properties"] for f in client.get("/api/events").json()["features"]}
    assert set(listed) == {"x:ongoing", "x:residual"}
    ongoing, residual = listed["x:ongoing"], listed["x:residual"]
    assert (ongoing["state"], ongoing["ongoing"], ongoing["subtype"]) == ("ongoing", True, "tense_protest")
    assert ongoing["risk"] == pytest.approx(0.4) and ongoing["last_confirmed_at"] is not None
    assert (residual["state"], residual["ongoing"]) == ("ended", False)
    assert residual["risk"] == pytest.approx(0.2, abs=0.001) and residual["half_life_min"] == 18 * 60
    everything = client.get("/api/events", params={"active": "false"}).json()["features"]
    capped = next(f["properties"] for f in everything if f["properties"]["external_ref"] == "x:capped")
    assert capped["risk"] == 0 and capped["state"] == "ended"
