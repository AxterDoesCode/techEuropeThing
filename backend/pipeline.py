"""Poll and rescore logic, independent of Modal and of the storage backend."""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any, Iterable, Protocol

from .models import CellScore, Event, RawItem, utcnow
from .scoring import FINE_RES, compute_cell_scores
from .sources.base import StructuredSource
from .sources.tfl_road import TflRoadSource

SOURCES: dict[str, StructuredSource] = {s.id: s for s in [TflRoadSource()]}


class Repo(Protocol):
    def get_cursor(self, source_id: str) -> dict[str, Any]: ...
    def upsert_raw_items(self, items: list[RawItem]) -> dict[str, int]: ...
    def upsert_structured_event(self, ev: Event) -> bool: ...
    def end_missing(self, source_id: str, seen_refs: Iterable[str], at: datetime) -> int: ...
    def start_run(self, source_id: str, started_at: datetime) -> int: ...
    def finish_run(
        self,
        run_id: int,
        source_id: str,
        finished_at: datetime,
        cursor: dict[str, Any] | None,
        counts: dict[str, int],
        error: str | None,
    ) -> None: ...
    def scoring_candidates(self, now: datetime) -> list[Event]: ...
    def baseline(self, res: int) -> dict[str, float]: ...
    def replace_cell_scores(self, scores: list[CellScore], now: datetime) -> None: ...


def run_poll(source: StructuredSource, repo: Repo) -> dict[str, int]:
    """One poll of one source. Always writes an agent_runs row, including on failure."""
    run_id = repo.start_run(source.id, utcnow())
    counts = {"fetched": 0, "inserted": 0, "ended": 0}
    cursor: dict[str, Any] | None = None
    error: str | None = None
    try:
        items, cursor = source.fetch(repo.get_cursor(source.id))
        counts["fetched"] = len(items)
        raw_ids = repo.upsert_raw_items(items)

        seen_refs: list[str] = []
        for item in items:
            ev = source.to_event(item)
            if ev is None:
                continue
            ev.raw_item_ids = [raw_ids[item.external_id]]
            seen_refs.append(ev.external_ref or "")
            if repo.upsert_structured_event(ev):
                counts["inserted"] += 1

        # An empty result from a snapshot feed is treated as an upstream fault,
        # not as every event having ended.
        if source.snapshot and items:
            counts["ended"] = repo.end_missing(source.id, seen_refs, utcnow())
    except Exception:
        error = traceback.format_exc(limit=3)
    repo.finish_run(run_id, source.id, utcnow(), cursor, counts, error)
    if error:
        raise RuntimeError(f"poll of {source.id} failed:\n{error}")
    return counts


def run_rescore(repo: Repo) -> int:
    now = utcnow()
    scores = compute_cell_scores(repo.scoring_candidates(now), now, repo.baseline(FINE_RES))
    repo.replace_cell_scores(scores, now)
    return len(scores)
