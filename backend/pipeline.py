"""Poll and rescore logic, independent of Modal and of the storage backend."""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any, Iterable, Protocol

from . import llm
from .models import CellScore, Event, RawItem, utcnow
from .scoring import FINE_RES, compute_cell_scores
from .sources.base import StructuredSource
from .sources.ea_floods import EaFloodsSource
from .sources.met_news import MetNewsSource
from .sources.rss import BbcLondonSource
from .sources.tfl_road import TflRoadSource
from .sources.tfl_transit import TflTransitSource

SOURCES: dict[str, StructuredSource] = {
    s.id: s
    for s in [
        TflRoadSource(),
        TflTransitSource(),
        EaFloodsSource(),
        MetNewsSource(),
        BbcLondonSource(),
    ]
}


def is_pollable(source_id: str) -> bool:
    """False for unknown sources and for sources that need an LLM when none is configured."""
    source = SOURCES.get(source_id)
    if source is None:
        return False
    return llm.is_configured() or not getattr(source, "requires_llm", False)


class Repo(Protocol):
    def get_cursor(self, source_id: str) -> dict[str, Any]: ...
    def upsert_raw_items(self, items: list[RawItem]) -> dict[str, int]: ...
    def upsert_structured_events(self, events: list[Event]) -> int: ...
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

        # Events are written in one call: the repo can be in another container
        events: list[Event] = []
        # Unstructured sources define to_events: several events per item, plus
        # the number of LLM requests made. The repo is the similar-event lookup.
        to_events = getattr(source, "to_events", None)
        lookup = repo if getattr(repo, "find_merge_candidates", None) else None
        llm_calls = 0
        for item in items:
            if to_events is not None:
                item_events, calls = to_events(item, lookup)
                llm_calls += calls
            else:
                ev = source.to_event(item)
                item_events = [ev] if ev is not None else []
            for ev in item_events:
                ev.raw_item_ids = [raw_ids[item.external_id]]
                events.append(ev)
        if llm_calls:
            counts["llm_calls"] = llm_calls
        # upsert_events also reports merges. Repos without it (test doubles) only
        # implement upsert_structured_events. `merged` is recorded when non-zero.
        upsert_events = getattr(repo, "upsert_events", None)
        if upsert_events is not None:
            stored = upsert_events(events)
            counts["inserted"] = stored["inserted"]
            if stored["merged"]:
                counts["merged"] = stored["merged"]
        else:
            counts["inserted"] = repo.upsert_structured_events(events)
        seen_refs = [ev.external_ref or "" for ev in events]

        # An empty result from a snapshot feed is treated as an upstream fault, not
        # as every event having ended, unless the source declares that an empty
        # feed is a normal state (e.g. no flood warnings in force).
        if source.snapshot and (items or getattr(source, "empty_is_valid", False)):
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
