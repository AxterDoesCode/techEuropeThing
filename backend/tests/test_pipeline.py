import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pytest

from backend.models import CellScore, Event, RawItem
from backend.pipeline import run_poll, run_rescore
from backend.scoring import event_risk
from backend.sources.tfl_road import TflRoadSource

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tfl_road.json").read_text())


class MemoryRepo:
    def __init__(self) -> None:
        self.raw: dict[tuple[str, str], int] = {}
        self.events: dict[str, Event] = {}
        self.runs: list[dict[str, Any]] = []
        self.cell_scores: list[CellScore] = []

    def get_cursor(self, source_id: str) -> dict[str, Any]:
        return {}

    def upsert_raw_items(self, items: list[RawItem]) -> dict[str, int]:
        for it in items:
            self.raw.setdefault((it.source_id, it.external_id), len(self.raw) + 1)
        return {it.external_id: self.raw[(it.source_id, it.external_id)] for it in items}

    def upsert_structured_event(self, ev: Event) -> bool:
        inserted = ev.external_ref not in self.events
        self.events[ev.external_ref] = ev
        return inserted

    def end_missing(self, source_id: str, seen_refs: Iterable[str], at: datetime) -> int:
        seen = set(seen_refs)
        n = 0
        for ref, ev in self.events.items():
            if ref.startswith(f"{source_id}:") and ref not in seen and ev.ended_at is None:
                ev.ended_at = at
                n += 1
        return n

    def start_run(self, source_id: str, started_at: datetime) -> int:
        self.runs.append({"source_id": source_id})
        return len(self.runs) - 1

    def finish_run(self, run_id, source_id, finished_at, cursor, counts, error) -> None:
        self.runs[run_id] |= {"counts": counts, "error": error}

    def scoring_candidates(self, now: datetime) -> list[Event]:
        return list(self.events.values())

    def baseline(self, res: int) -> dict[str, float]:
        return {}

    def replace_cell_scores(self, scores: list[CellScore], now: datetime) -> None:
        self.cell_scores = scores


class FixtureSource(TflRoadSource):
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads

    def fetch(self, cursor):
        items = [RawItem(source_id=self.id, external_id=d["id"], payload=d) for d in self.payloads]
        return items, cursor


def test_to_event_maps_fields():
    src = TflRoadSource()
    events = {
        d["id"]: src.to_event(RawItem(source_id="tfl_road", external_id=d["id"], payload=d))
        for d in FIXTURE
    }
    serious = events["TIMS-206772"]
    assert serious.severity == 0.55
    assert serious.geometry["type"] == "MultiPolygon"
    assert serious.external_ref == "tfl_road:TIMS-206772"
    assert serious.half_life_min is None
    assert serious.occurred_at.tzinfo is not None
    assert serious.title.startswith("TfL works: [A12]")
    # "No impact" items produce no event
    assert [i for i, e in events.items() if e is None] == ["TEST-NO-IMPACT"]
    # items without a polygon fall back to the point
    assert any(e and e.geometry["type"] == "Point" for e in events.values())


def test_poll_is_idempotent_and_marks_ended():
    repo = MemoryRepo()
    first = run_poll(FixtureSource(FIXTURE), repo)
    expected = len(FIXTURE) - 1
    assert first == {"fetched": len(FIXTURE), "inserted": expected, "ended": 0}

    second = run_poll(FixtureSource(FIXTURE), repo)
    assert second["inserted"] == 0 and second["ended"] == 0
    assert len(repo.events) == expected

    remaining = [d for d in FIXTURE if d["id"] != "TIMS-206772"]
    third = run_poll(FixtureSource(remaining), repo)
    assert third["ended"] == 1
    ended = repo.events["tfl_road:TIMS-206772"]
    assert ended.ended_at is not None
    assert event_risk(ended, ended.ended_at) > 0


def test_empty_fetch_does_not_end_events():
    repo = MemoryRepo()
    run_poll(FixtureSource(FIXTURE), repo)
    assert run_poll(FixtureSource([]), repo)["ended"] == 0
    assert all(e.ended_at is None for e in repo.events.values())


def test_failed_poll_is_recorded_and_raised():
    class Broken(TflRoadSource):
        def fetch(self, cursor):
            raise ConnectionError("upstream down")

    repo = MemoryRepo()
    with pytest.raises(RuntimeError):
        run_poll(Broken(), repo)
    assert "upstream down" in repo.runs[0]["error"]


def test_rescore_writes_cells():
    repo = MemoryRepo()
    run_poll(FixtureSource(FIXTURE), repo)
    assert run_rescore(repo) == len(repo.cell_scores) > 0
