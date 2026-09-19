import json
from datetime import timedelta
from pathlib import Path

import pytest

from backend import db, geocode
from backend.db import SqliteRepo
from backend.models import RawItem, utcnow
from backend.pipeline import run_poll, run_rescore
from backend.tests.test_pipeline import FIXTURE, FixtureSource


@pytest.fixture
def repo(tmp_path):
    db.connect(tmp_path / "risk.sqlite")
    return SqliteRepo()


def test_poll_is_idempotent_and_marks_ended(repo):
    expected = len(FIXTURE) - 1
    assert run_poll(FixtureSource(FIXTURE), repo)["inserted"] == expected
    before = {e.external_ref: e for e in db.events_geojson(utcnow())}

    again = run_poll(FixtureSource(FIXTURE), repo)
    assert again == {"fetched": len(FIXTURE), "inserted": 0, "ended": 0}

    remaining = [d for d in FIXTURE if d["id"] != "TIMS-206772"]
    assert run_poll(FixtureSource(remaining), repo)["ended"] == 1
    after = {e.external_ref: e for e in db.events_geojson(utcnow())}
    assert after["tfl_road:TIMS-206772"].ended_at is not None
    assert after["tfl_road:TIMS-206772"].id == before["tfl_road:TIMS-206772"].id

    # reappearing upstream clears ended_at
    run_poll(FixtureSource(FIXTURE), repo)
    assert db.event_by_id(before["tfl_road:TIMS-206772"].id).ended_at is None


def test_event_round_trip_preserves_fields(repo):
    run_poll(FixtureSource(FIXTURE), repo)
    ev = next(e for e in db.events_geojson(utcnow()) if e.external_ref == "tfl_road:TIMS-206772")
    assert ev.geometry["type"] == "MultiLineString"
    assert ev.occurred_at.utcoffset() == timedelta(0)
    assert ev.source_ids == ["tfl_road"] and len(ev.raw_item_ids) == 1
    assert ev.source_confidence == {"tfl_road": 0.95}


def test_changed_upstream_item_updates_event_and_raw_item(repo):
    run_poll(FixtureSource(FIXTURE), repo)
    changed = json.loads(json.dumps(FIXTURE))
    changed[0]["severity"] = "Severe"
    assert run_poll(FixtureSource(changed), repo)["inserted"] == 0
    ev = next(e for e in db.events_geojson(utcnow()) if e.external_ref == f"tfl_road:{changed[0]['id']}")
    assert ev.severity == 0.8


def test_raw_items_keep_ids_across_polls(repo):
    items = [RawItem(source_id="tfl_road", external_id="a", payload={"v": 1})]
    first = repo.upsert_raw_items(items)
    items[0].payload["v"] = 2
    assert repo.upsert_raw_items(items) == first


def test_rescore_and_cell_queries(repo):
    run_poll(FixtureSource(FIXTURE), repo)
    assert run_rescore(repo) > 0
    fine = db.cell_scores(9, 0.0)
    assert fine and all(0 <= c.score <= 1 for c in fine)
    assert any(c.top_event_ids for c in fine)
    assert db.cell_scores(9, 0.0, bbox=(-10, 0, -9, 1)) == []


def test_due_sources_and_agent_status(repo):
    assert "tfl_road" in repo.due_sources(utcnow())
    run_poll(FixtureSource(FIXTURE), repo)
    assert "tfl_road" not in repo.due_sources(utcnow())
    assert "tfl_road" in repo.due_sources(utcnow() + timedelta(seconds=121))
    status = {a["id"]: a for a in db.agent_status()}
    assert status["tfl_road"]["last_status"] == "ok"
    assert status["tfl_road"]["runs_last_hour"] == 1
    assert status["tfl_road"]["inserted_last_hour"] == len(FIXTURE) - 1


def test_crime_points_and_baseline(repo):
    assert db.latest_crime_points_json() is None
    repo.save_crime_points("2026-06", {"month": "2026-06", "rows": []})
    repo.save_crime_points("2026-07", {"month": "2026-07", "rows": [[0, 51, 1, 1.0, "x", {}]]})
    assert json.loads(db.latest_crime_points_json())["month"] == "2026-07"
    repo.replace_baseline({"89195da49b7ffff": 0.5}, 9)
    assert repo.baseline(9) == {"89195da49b7ffff": 0.5}


def test_geocode_results_persist_including_misses(repo, monkeypatch):
    calls = []

    def fake_nominatim(text):
        calls.append(text)
        return geocode.GeoResult(-0.11, 51.53, 150.0, "x", "test") if "Baker" in text else None

    monkeypatch.setattr(geocode, "_nominatim", fake_nominatim)
    monkeypatch.setattr(geocode, "_cache", {})
    geocode.use_cache(repo)
    try:
        assert geocode.geocode("Lloyd Baker Street").lat == 51.53
        assert geocode.geocode("Nowhere Road") is None
        monkeypatch.setattr(geocode, "_cache", {})  # a new process
        assert geocode.geocode("lloyd  baker street").lat == 51.53
        assert geocode.geocode("Nowhere Road") is None
        assert calls == ["Lloyd Baker Street", "Nowhere Road"]
    finally:
        geocode.use_cache(None)


def test_snapshot_is_a_usable_copy(repo, tmp_path):
    run_poll(FixtureSource(FIXTURE), repo)
    dest = tmp_path / "volume" / "risk.sqlite"
    db.snapshot(dest)
    db.connect(dest)
    assert len(db.events_geojson(utcnow())) == len(FIXTURE) - 1
    assert not Path(str(dest) + ".tmp").exists()
