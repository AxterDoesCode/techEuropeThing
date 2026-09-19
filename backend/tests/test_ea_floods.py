import json
from pathlib import Path

import httpx
from shapely.geometry import shape

from backend.models import Category, RawItem
from backend.pipeline import run_poll
from backend.scoring import event_risk
from backend.sources import ea_floods
from backend.sources.ea_floods import EaFloodsSource
from backend.tests.test_pipeline import MemoryRepo

# Fixture provenance. No flood warning was in force anywhere in England when the
# fixture was recorded (2026-09-19), so /id/floods returned no items.
# - Real: every flood area (floodAreaID, description, eaAreaName, the floodArea
#   object) and every `area` value, which is the unmodified output of
#   ea_floods._area() for that flood area (simplified polygon plus lat/long
#   from /id/floodAreas/{id}).
# - Synthetic: the warning fields of all items (severity, severityLevel,
#   message, isTidal, time*), laid out as in the API reference.
# - 064FWB40Barnes: `area.geometry` removed, the state after a failed polygon
#   request.
# - 062FWF55Romford: severityLevel 4, no `area`; must be skipped.
# - 122WAF946: a flood area near York, outside London; must be skipped.
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ea_floods.json").read_text())
SKIPPED = ["062FWF55Romford", "122WAF946"]


class FixtureSource(EaFloodsSource):
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads

    def fetch(self, cursor):
        items = [
            RawItem(source_id=self.id, external_id=d["floodAreaID"], payload=d)
            for d in self.payloads
        ]
        return items, cursor


def _events():
    src = EaFloodsSource()
    return {
        d["floodAreaID"]: src.to_event(
            RawItem(source_id="ea_floods", external_id=d["floodAreaID"], payload=d)
        )
        for d in FIXTURE
    }


def test_to_event_maps_fields():
    events = _events()
    severe = events["064FWF41Morden"]
    assert severe.external_ref == "ea_floods:064FWF41Morden"
    assert severe.category == Category.FLOOD
    assert severe.title == "Flood warning: River Wandle at Morden"
    assert severe.summary.startswith("Fixture message for River Wandle at Morden")
    assert severe.severity == 0.9
    assert severe.confidence == 0.95
    assert severe.source_confidence == {"ea_floods": 0.95}
    assert severe.half_life_min is None
    assert severe.radius_m == 100
    assert (severe.lng, severe.lat) == (-0.17943, 51.39925)
    # naive upstream timestamps are read as UTC
    assert severe.occurred_at.isoformat() == "2026-09-18T10:30:00+00:00"
    assert severe.source_ids == ["ea_floods"]
    assert severe.urls == [
        "http://environment.data.gov.uk/flood-monitoring/id/floods/064FWF41Morden"
    ]
    assert events["064FWB41Wndswrth"].severity == 0.6
    assert events["064WAF41Wandle"].severity == 0.3


def test_polygon_geometry_is_valid():
    events = _events()
    polygons = [e for e in events.values() if e and e.geometry["type"] != "Point"]
    assert {e.geometry["type"] for e in polygons} == {"Polygon", "MultiPolygon"}
    for e in polygons:
        geom = shape(e.geometry)
        assert geom.is_valid and not geom.is_empty
        # [lng, lat] order: the centroid lies inside the polygon's bounds
        w, s, east, n = geom.bounds
        assert w <= e.lng <= east and s <= e.lat <= n


def test_missing_polygon_falls_back_to_point():
    ev = _events()["064FWB40Barnes"]
    assert ev.geometry == {"type": "Point", "coordinates": [-0.23741, 51.47112]}


def test_not_in_force_and_outside_london_are_skipped():
    assert [i for i, e in _events().items() if e is None] == SKIPPED


def test_area_falls_back_to_lat_long_when_polygon_request_fails(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("/polygon"):
            return httpx.Response(503, request=request)
        return httpx.Response(200, json={"items": {"lat": 51.4, "long": -0.2}}, request=request)

    monkeypatch.setattr(ea_floods.httpx, "get", fake_get)
    monkeypatch.setattr(ea_floods, "_AREA_CACHE", {})
    assert ea_floods._area("X") == {"lng": -0.2, "lat": 51.4}
    # failures are not cached
    assert ea_floods._AREA_CACHE == {}
    ea_floods._area("X")
    assert len(calls) == 4


def test_area_simplifies_and_caches_polygon(monkeypatch):
    ring = [[-0.2, 51.4], [-0.19, 51.4], [-0.19, 51.41], [-0.195, 51.41001], [-0.2, 51.41], [-0.2, 51.4]]
    collection = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}}],
    }
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        body = collection if url.endswith("/polygon") else {"items": {"lat": 51.405, "long": -0.195}}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(ea_floods.httpx, "get", fake_get)
    monkeypatch.setattr(ea_floods, "_AREA_CACHE", {})
    area = ea_floods._area("X")
    assert (area["lng"], area["lat"]) == (-0.195, 51.405)
    # the near-collinear vertex is removed by simplification
    assert len(area["geometry"]["coordinates"][0]) == 5
    assert ea_floods._area("X") is area
    assert len(calls) == 2


def test_poll_is_idempotent_and_marks_ended():
    repo = MemoryRepo()
    expected = len(FIXTURE) - len(SKIPPED)
    first = run_poll(FixtureSource(FIXTURE), repo)
    assert first == {"fetched": len(FIXTURE), "inserted": expected, "ended": 0}

    second = run_poll(FixtureSource(FIXTURE), repo)
    assert second["inserted"] == 0 and second["ended"] == 0
    assert len(repo.events) == expected

    # Upstream keeps a lifted warning in the list at severityLevel 4 for a
    # period; that must end the event in the same way as its removal.
    lifted = [
        d | {"severityLevel": 4, "severity": "Warning no longer in force"}
        if d["floodAreaID"] == "064FWF41Morden"
        else d
        for d in FIXTURE
    ]
    third = run_poll(FixtureSource(lifted), repo)
    assert third["ended"] == 1
    ended = repo.events["ea_floods:064FWF41Morden"]
    assert ended.ended_at is not None
    assert event_risk(ended, ended.ended_at) > 0

    remaining = [d for d in FIXTURE if d["floodAreaID"] != "064FWB41Wndswrth"]
    assert run_poll(FixtureSource(remaining), repo)["ended"] == 1
    assert repo.events["ea_floods:064FWB41Wndswrth"].ended_at is not None
