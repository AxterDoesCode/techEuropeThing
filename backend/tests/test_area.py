from datetime import datetime, timezone

import h3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import api_platform, area, db
from backend.db import SqliteRepo
from backend.models import Category, CellScore, Event
from backend.scoring import FINE_RES, distance_m

SOHO = (-0.1365, 51.5136)
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def test_cells_within_contains_home_and_respects_radius():
    cells = area.cells_within(*SOHO, 400)
    assert h3.latlng_to_cell(SOHO[1], SOHO[0], FINE_RES) in cells
    assert len(cells) == len(set(cells)) and 3 <= len(cells) <= 12
    for c in cells[1:]:
        lat, lng = h3.cell_to_latlng(c)
        assert distance_m(*SOHO, lng, lat) <= 400
    assert len(area.cells_within(*SOHO, 50)) == 1
    assert len(area.cells_within(*SOHO, 1500)) > len(cells)


def test_percentile_and_risk_summary():
    assert area.percentile([], 0.5) == 0.0
    assert area.percentile([0.1, 0.2, 0.3, 0.4], 0.35) == 0.75
    cells = ["a", "b"]
    summary = area.risk_summary(cells, {"a": {"score": 0.6, "live": 0.2, "baseline": 0.5}}, [0.1, 0.2, 0.3, 0.9])
    assert summary["mean_score"] == 0.3 and summary["max_score"] == 0.6
    assert summary["london_percentile"] == 0.5 and summary["cells"] == 2


def test_crime_summary_counts_only_points_inside():
    rows = [
        [SOHO[0], SOHO[1], 10, 6.0, "On or near Old Compton Street", {"violent-crime": 4, "other-theft": 3}],
        [SOHO[0] + 0.001, SOHO[1], 5, 2.0, "On or near Dean Street", {"other-theft": 5}],
        [SOHO[0] + 0.05, SOHO[1], 99, 50.0, "On or near Far Road", {"robbery": 99}],
    ]
    s = area.crime_summary(rows, "2026-07", *SOHO, 400)
    assert s["recorded_crimes"] == 15 and s["weighted"] == 8.0 and s["month"] == "2026-07"
    assert s["top_categories"] == {"other-theft": 8, "violent-crime": 4}
    assert s["top_streets"][0] == {"street": "On or near Old Compton Street", "recorded_crimes": 10}


def _event(lng, lat, **kw) -> Event:
    base = dict(
        external_ref=f"test:{lng},{lat}", category=Category.VIOLENT_CRIME, title="t",
        geometry={"type": "Point", "coordinates": [lng, lat]}, lng=lng, lat=lat, radius_m=250,
        severity=0.9, confidence=0.9, half_life_min=1440, occurred_at=NOW, source_ids=["met_news"],
    )
    return Event(**(base | kw))


def test_events_within_uses_event_radius_and_drops_decayed():
    near = _event(SOHO[0] + 0.004, SOHO[1])  # ~280 m away
    reaches = _event(SOHO[0] + 0.008, SOHO[1])  # ~555 m away; 400 + 250 m radius still reaches
    far = _event(SOHO[0] + 0.02, SOHO[1])
    old = _event(SOHO[0], SOHO[1], occurred_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    found = area.events_within([near, reaches, far, old], *SOHO, 400, NOW)
    assert [f["properties"]["external_ref"] for f in found] == [near.external_ref, reaches.external_ref]
    assert found[0]["properties"]["distance_m"] == pytest.approx(277, abs=5)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.connect(tmp_path / "risk.sqlite")
    monkeypatch.setattr(api_platform, "_cache", {})
    repo = SqliteRepo()
    home = h3.latlng_to_cell(SOHO[1], SOHO[0], FINE_RES)
    repo.replace_cell_scores([CellScore(h3=home, res=FINE_RES, live=0.2, baseline=0.8, score=0.46)], NOW)
    repo.save_crime_points("2026-07", {"month": "2026-07", "rows": [[SOHO[0], SOHO[1], 7, 3.5, "On or near Soho Square", {"robbery": 2}]]})
    repo.upsert_structured_events([_event(SOHO[0], SOHO[1], occurred_at=db.utcnow())])
    app = FastAPI()
    app.include_router(api_platform.router)
    return TestClient(app)


def test_area_endpoint(client):
    body = client.get("/api/area", params={"lat": SOHO[1], "lng": SOHO[0], "radius_m": 300}).json()
    assert body["risk"]["max_score"] == 0.46 and body["risk"]["cells"] >= 1
    assert body["crime"]["recorded_crimes"] == 7 and body["crime"]["month"] == "2026-07"
    assert len(body["events"]) == 1 and body["events"][0]["properties"]["risk"] > 0.5
    assert client.get("/api/area", params={"lat": 53.48, "lng": -2.24}).status_code == 422
    assert client.get("/api/area", params={"lat": SOHO[1], "lng": SOHO[0], "radius_m": 10}).status_code == 422
