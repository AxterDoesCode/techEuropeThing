import json
import threading
from pathlib import Path

import httpx
import pytest
from shapely.geometry import shape

from backend.models import LONDON_BBOX, Category, RawItem, in_london, utcnow
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
# - 064FWF41Morden: timeRaised is later than timeSeverityChanged (a review that
#   did not change the severity).
# - 064WAF41Wandle: no isTidal. 064FWB40Barnes: no timeSeverityChanged.
# - 064WAT1ThamesEst: real id, description and lat/long (centroid east of the
#   bbox); the polygon is a synthetic rectangle that crosses the bbox's east
#   edge, because the API was not reachable when this item was added. Must be
#   kept.
# - 064FWB40Barnes: `area.geometry` removed, the state after a failed polygon
#   request.
# - 062FWF55Romford: severityLevel 4, no `area`; must be skipped.
# - 122WAF946: a flood area near York, polygon and centroid outside London;
#   must be skipped.
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
    assert severe.title == "Severe flood warning: River Wandle at Morden"
    assert severe.summary.startswith("Non-tidal (river or groundwater). Fixture message for River Wandle at Morden")
    assert severe.severity == 0.9
    assert severe.confidence == 0.95
    assert severe.source_confidence == {"ea_floods": 0.95}
    assert severe.half_life_min is None
    assert severe.radius_m == 100
    assert (severe.lng, severe.lat) == (-0.17943, 51.39925)
    # timeSeverityChanged, not the later timeRaised; naive timestamps are read as UTC
    assert severe.occurred_at.isoformat() == "2026-09-18T10:30:00+00:00"
    assert severe.source_ids == ["ea_floods"]
    assert severe.urls == [
        "http://environment.data.gov.uk/flood-monitoring/id/floods/064FWF41Morden"
    ]
    assert events["064FWB41Wndswrth"].severity == 0.6
    assert events["064WAF41Wandle"].severity == 0.3


def test_title_uses_the_severity_label():
    events = _events()
    assert events["064FWB41Wndswrth"].title == "Flood warning: River Wandle at Wandsworth"
    assert events["064WAF41Wandle"].title.startswith("Flood alert: River Wandle area")
    # level-based label when the payload has no severity text
    d = {k: v for k, v in FIXTURE[0].items() if k != "severity"}
    ev = EaFloodsSource().to_event(RawItem(source_id="ea_floods", external_id="x", payload=d))
    assert ev.title == "Severe flood warning: River Wandle at Morden"


def test_summary_states_tidal_when_known():
    events = _events()
    assert events["064FWB41Wndswrth"].summary.startswith("Tidal. Fixture message")
    assert events["064FWF41Morden"].summary.startswith("Non-tidal (river or groundwater). ")
    assert events["064WAF41Wandle"].summary.startswith("Fixture message")


def test_start_time_falls_back_to_time_raised_then_now():
    assert _events()["064FWB40Barnes"].occurred_at.isoformat() == "2026-09-18T13:30:00+00:00"
    d = {k: v for k, v in FIXTURE[0].items() if k not in ("timeSeverityChanged", "timeRaised")}
    before = utcnow()
    ev = EaFloodsSource().to_event(RawItem(source_id="ea_floods", external_id="x", payload=d))
    assert before <= ev.occurred_at <= utcnow()


def test_polygon_reaching_into_the_bbox_is_kept_with_centroid_outside():
    ev = _events()["064WAT1ThamesEst"]
    assert not in_london(ev.lng, ev.lat)
    assert ev.geometry["type"] == "Polygon"
    assert shape(ev.geometry).intersects(ea_floods.LONDON_BOX)
    # without the polygon the centroid decides, and the item is dropped
    d = next(d for d in FIXTURE if d["floodAreaID"] == "064WAT1ThamesEst")
    d = d | {"area": {"lng": d["area"]["lng"], "lat": d["area"]["lat"]}}
    assert EaFloodsSource().to_event(RawItem(source_id="ea_floods", external_id="x", payload=d)) is None


def test_query_covers_the_bbox_corners():
    assert ea_floods.PARAMS == {"lat": "51.48935", "long": "-0.08820", "dist": "45"}
    w, s, e, n = LONDON_BBOX
    lat, lng = float(ea_floods.PARAMS["lat"]), float(ea_floods.PARAMS["long"])
    corners = [ea_floods._haversine_km(lat, lng, y, x) for y in (s, n) for x in (w, e)]
    assert 36.8 < max(corners) < 37.0


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


RING = [[-0.2, 51.4], [-0.19, 51.4], [-0.19, 51.41], [-0.195, 51.41001], [-0.2, 51.41], [-0.2, 51.4]]


def _collection(*rings):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [r]}} for r in rings
        ],
    }


def _fake_api(monkeypatch, respond):
    """Replace httpx.get in the module with `respond(url) -> (status, body)`.
    Returns the list of requested URLs."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        status, body = respond(url)
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(ea_floods.httpx, "get", fake_get)
    monkeypatch.setattr(ea_floods, "_AREA_CACHE", {})
    return calls


def test_area_falls_back_to_lat_long_when_polygon_request_fails(monkeypatch):
    calls = _fake_api(
        monkeypatch,
        lambda url: (503, {}) if url.endswith("/polygon") else (200, {"items": {"lat": 51.4, "long": -0.2}}),
    )
    assert ea_floods._areas(["X"]) == {"X": {"lng": -0.2, "lat": 51.4}}
    # failures are not cached
    assert ea_floods._AREA_CACHE == {}
    ea_floods._areas(["X"])
    assert len(calls) == 4


def test_area_simplifies_and_caches_polygon(monkeypatch):
    calls = _fake_api(
        monkeypatch,
        lambda url: (200, _collection(RING) if url.endswith("/polygon") else {"items": {"lat": 51.405, "long": -0.195}}),
    )
    area = ea_floods._areas(["X"])["X"]
    assert (area["lng"], area["lat"]) == (-0.195, 51.405)
    # the near-collinear vertex is removed by simplification
    assert len(area["geometry"]["coordinates"][0]) == 5
    assert ea_floods._areas(["X"])["X"] is area
    assert len(calls) == 2


def test_get_all_reports_a_failed_request_as_none(monkeypatch):
    def respond(url):
        if url == "u/raise":
            raise httpx.ConnectTimeout("timed out")
        return (500, {}) if url == "u/500" else (200, {"url": url})

    _fake_api(monkeypatch, respond)
    urls = ["u/1", "u/raise", "u/2", "u/500", "u/3", "u/4", "u/5"]
    assert ea_floods._get_all(urls) == {
        u: None if u in ("u/raise", "u/500") else {"url": u} for u in urls
    }


def test_get_all_skips_requests_not_started_within_the_budget(monkeypatch):
    release = threading.Event()

    def respond(url):
        release.wait(5)
        return 200, {}

    calls = _fake_api(monkeypatch, respond)
    urls = [f"u/{i}" for i in range(ea_floods.AREA_WORKERS + 3)]
    try:
        assert ea_floods._get_all(urls, budget_s=0.05) == dict.fromkeys(urls)
    finally:
        release.set()
    assert len(calls) == ea_floods.AREA_WORKERS


def test_one_failing_area_does_not_affect_the_others(monkeypatch):
    def respond(url):
        if "/BAD" in url:
            return 503, {}
        return 200, _collection(RING) if url.endswith("/polygon") else {"items": {"lat": 51.405, "long": -0.195}}

    _fake_api(monkeypatch, respond)
    areas = ea_floods._areas(["A", "BAD", "B"])
    assert areas["BAD"] == {}
    assert "geometry" in areas["A"] and "geometry" in areas["B"]
    assert set(ea_floods._AREA_CACHE) == {"A", "B"}


def test_invalid_upstream_polygon_is_repaired(monkeypatch):
    # self-intersecting ring (two triangles that meet at one point)
    bowtie = [[-0.2, 51.4], [-0.18, 51.42], [-0.18, 51.4], [-0.2, 51.42], [-0.2, 51.4]]
    assert not shape({"type": "Polygon", "coordinates": [bowtie]}).is_valid
    _fake_api(
        monkeypatch,
        lambda url: (200, _collection(bowtie, RING) if url.endswith("/polygon") else {"items": {"lat": 51.41, "long": -0.19}}),
    )
    geom = shape(ea_floods._areas(["X"])["X"]["geometry"])
    assert geom.is_valid and not geom.is_empty and geom.geom_type in ("Polygon", "MultiPolygon")


def test_unusable_polygon_body_is_not_fatal(monkeypatch):
    _fake_api(
        monkeypatch,
        lambda url: (200, {"features": [{"geometry": {"type": "Polygon", "coordinates": "x"}}]})
        if url.endswith("/polygon")
        else (200, {"items": {"lat": 51.4, "long": -0.2}}),
    )
    assert ea_floods._areas(["X"]) == {"X": {"lng": -0.2, "lat": 51.4}}


def test_area_lookups_per_poll_are_capped(monkeypatch, caplog):
    calls = _fake_api(
        monkeypatch,
        lambda url: (200, _collection(RING) if url.endswith("/polygon") else {"items": {"lat": 51.405, "long": -0.195}}),
    )
    monkeypatch.setattr(ea_floods, "MAX_AREA_LOOKUPS", 3)
    ids = [f"A{i}" for i in range(5)]
    with caplog.at_level("WARNING", logger=ea_floods.__name__):
        assert list(ea_floods._areas(ids)) == ids[:3]
    assert "5 uncached flood areas, looking up 3" in caplog.text
    assert len(calls) == 6
    # the next poll looks up the remaining areas
    assert list(ea_floods._areas(ids)) == ids
    assert len(calls) == 10


def _floods_response(items):
    def respond(url):
        if url == ea_floods.URL:
            return 200, {"items": items}
        return 200, _collection(RING) if url.endswith("/polygon") else {"items": {"lat": 51.405, "long": -0.195}}

    return respond


def test_fetch_attaches_areas_to_items_in_force(monkeypatch):
    upstream = [{k: v for k, v in d.items() if k != "area"} for d in FIXTURE]
    calls = _fake_api(monkeypatch, _floods_response(upstream))
    items, cursor = EaFloodsSource().fetch({"k": 1})
    assert cursor == {"k": 1}
    assert [i.external_id for i in items] == [d["floodAreaID"] for d in FIXTURE]
    by_id = {i.external_id: i.payload for i in items}
    assert "area" not in by_id["062FWF55Romford"]
    assert by_id["064FWF41Morden"]["area"]["geometry"]["type"] == "Polygon"
    # one floods request, then two requests per item in force
    assert len(calls) == 1 + 2 * (len(FIXTURE) - 1)


@pytest.mark.parametrize("status", [403, 429, 500])
def test_blocked_poll_fails_and_ends_nothing(monkeypatch, status):
    repo = MemoryRepo()
    run_poll(FixtureSource(FIXTURE), repo)
    active = [ref for ref, ev in repo.events.items() if ev.ended_at is None]
    assert active

    _fake_api(monkeypatch, lambda url: (status, {}))
    with pytest.raises(RuntimeError, match=str(status)):
        run_poll(EaFloodsSource(), repo)
    assert [ref for ref, ev in repo.events.items() if ev.ended_at is None] == active


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
