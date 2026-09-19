import json
from datetime import datetime, timezone
from pathlib import Path

from backend.models import Category
from backend.pipeline import run_poll
from backend.sources.tfl_transit import TflTransitSource
from backend.tests.test_pipeline import MemoryRepo

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tfl_transit.json").read_text())

CLOSURE = "940GZZLUBSC:Closure:2026-09-19T00:00:00Z"
# Standing advice, outside the London bbox, no coordinates
SKIPPED = 3


class FixtureSource(TflTransitSource):
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads

    def fetch(self, cursor):
        return self.items(self.payloads), cursor


def _events(payloads: list[dict]) -> dict:
    src = TflTransitSource()
    return {raw.external_id: src.to_event(raw) for raw in src.items(payloads)}


def test_to_event_maps_fields():
    events = _events(FIXTURE)
    closure = events[CLOSURE]
    assert closure.category == Category.TRANSIT_DISRUPTION
    assert closure.severity == 0.6
    assert closure.confidence == 0.95
    assert closure.source_confidence == {"tfl_transit": 0.95}
    assert closure.external_ref == f"tfl_transit:{CLOSURE}"
    assert closure.title == "Closure: Barons Court Underground Station"
    assert closure.geometry == {"type": "Point", "coordinates": [-0.213427, 51.490311]}
    assert (closure.lng, closure.lat) == (-0.213427, 51.490311)
    assert closure.radius_m == 200
    assert closure.half_life_min is None
    assert closure.occurred_at == datetime(2026, 9, 19, 0, 0, tzinfo=timezone.utc)
    assert closure.expires_at == datetime(2026, 9, 20, 0, 29, tzinfo=timezone.utc)
    assert closure.source_ids == ["tfl_transit"]

    severities = {e.title.split(":")[0]: e.severity for e in events.values() if e}
    assert severities == {
        "Closure": 0.6,
        "Part Closure": 0.4,
        "Exit Only": 0.3,
        "Interchange Message": 0.15,
    }


def test_skipped_items():
    events = _events(FIXTURE)
    skipped = {i.split(":")[0] for i, e in events.items() if e is None}
    # Waterloo: appearance "Information". Cheshunt: north of the London bbox.
    assert skipped == {"940GZZLUWLO", "910GCHESHNT", "TEST-NO-COORDS"}
    assert len(skipped) == SKIPPED


def test_external_ids_are_stable_and_unique():
    src = TflTransitSource()
    first = [raw.external_id for raw in src.items(FIXTURE)]
    second = [raw.external_id for raw in src.items(list(reversed(FIXTURE)))]
    assert sorted(first) == sorted(second)
    assert len(set(first)) == len(FIXTURE)
    # Two disruptions at the same stop differ by type and start date
    assert CLOSURE in first
    assert "940GZZLUBSC:Part Closure:2026-07-06T03:30:00Z" in first


def test_colliding_ids_get_description_hash():
    a = dict(FIXTURE[0])
    b = {**a, "description": "A second notice with the same stop, type and start."}
    ids = [raw.external_id for raw in TflTransitSource().items([a, b])]
    assert len(set(ids)) == 2
    assert all(i.startswith(CLOSURE + ":") for i in ids)
    assert ids == [raw.external_id for raw in TflTransitSource().items([a, b])]


def test_poll_is_idempotent_and_marks_ended():
    repo = MemoryRepo()
    first = run_poll(FixtureSource(FIXTURE), repo)
    expected = len(FIXTURE) - SKIPPED
    assert first == {"fetched": len(FIXTURE), "inserted": expected, "ended": 0}

    second = run_poll(FixtureSource(FIXTURE), repo)
    assert second["inserted"] == 0 and second["ended"] == 0
    assert len(repo.events) == expected

    remaining = [d for d in FIXTURE if d["type"] != "Closure"]
    third = run_poll(FixtureSource(remaining), repo)
    assert third["ended"] == 1
    assert repo.events[f"tfl_transit:{CLOSURE}"].ended_at is not None
