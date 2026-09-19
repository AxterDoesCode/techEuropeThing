"""Poll and rescore logic, independent of Modal and of the storage backend."""

from __future__ import annotations

import traceback
from datetime import datetime
from typing import Any, Callable, Iterable, Protocol

from . import extraction, llm
from .models import CellScore, Event, RawItem, utcnow
from .scoring import FINE_RES, compute_cell_scores
from .sources.base import StructuredSource
from .sources.ea_floods import EaFloodsSource
from .sources.met_news import MetNewsSource
from .sources.rss import BbcLondonSource, EveningStandardSource, MyLondonSource
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
        EveningStandardSource(),
        MyLondonSource(),
    ]
}


def is_pollable(source_id: str) -> bool:
    """False for unknown sources and for sources that need an LLM when none is configured."""
    source = SOURCES.get(source_id)
    if source is None:
        return False
    return llm.is_configured() or not getattr(source, "requires_llm", False)


def _items_to_extract(
    source: StructuredSource, repo: "Repo", items: list[RawItem]
) -> tuple[list[RawItem], set[str]]:
    """Items to extract in this poll, and the ids of pending items deferred to later
    polls. Items to extract are pre-filter candidates not already extracted by the
    current extractor. Repos without the bookkeeping methods (test doubles) extract
    every candidate on every poll."""
    is_candidate = getattr(source, "is_candidate", None)
    candidates = [i for i in items if is_candidate is None or is_candidate(i)]
    pending_extraction = getattr(repo, "pending_extraction", None)
    if pending_extraction is None:
        return candidates, set()
    pending = set(
        pending_extraction(source.id, [i.external_id for i in candidates], extraction.extractor_id())
    )
    # Feeds list the newest items first. The cap bounds the duration of one poll;
    # items beyond it stay pending and are extracted by the following polls.
    todo = [i for i in candidates if i.external_id in pending]
    deferred = {i.external_id for i in todo[MAX_EXTRACTIONS_PER_POLL:]}
    return todo[:MAX_EXTRACTIONS_PER_POLL], deferred


def _extract_one(to_events: Callable[..., tuple[list[Event], int]], item: RawItem, lookup: Any):
    try:
        return to_events(item, lookup)
    except extraction.ExtractionFailed:
        return None


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


# Upper bound of LLM extractions started by one poll. With 4 extraction containers and
# at most 90 s per item, a poll then takes at most about 3 minutes.
MAX_EXTRACTIONS_PER_POLL = 8

# Runs the extraction of several items and returns, per item, its events and the
# number of LLM requests, or None when that item's extraction failed. On Modal this
# starts one container per item.
ItemMapper = Callable[[list[RawItem]], list[tuple[list[Event], int] | None]]


def run_poll(source: StructuredSource, repo: Repo, map_items: ItemMapper | None = None) -> dict[str, int]:
    """One poll of one source. Always writes an agent_runs row, including on failure.

    For an unstructured source (one that defines `to_events`), only items that
    pass the source's pre-filter and have not yet been extracted in their current
    version are extracted. `map_items` replaces the sequential extraction loop."""
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
        llm_calls = 0
        failed: set[str] = set()
        deferred: set[str] = set()
        to_events = getattr(source, "to_events", None)
        if to_events is None:
            per_item = [(item, [ev] if (ev := source.to_event(item)) is not None else []) for item in items]
        else:
            todo, deferred = _items_to_extract(source, repo, items)
            if map_items is not None:
                results = map_items(todo)
            else:
                # The repo is the similar-event lookup of the extraction agent
                lookup = repo if getattr(repo, "find_merge_candidates", None) else None
                results = [_extract_one(to_events, item, lookup) for item in todo]
            # A failed item stays pending and is extracted again on the next poll
            failed = {item.external_id for item, r in zip(todo, results) if r is None}
            done = [(item, r) for item, r in zip(todo, results) if r is not None]
            per_item = [(item, item_events) for item, (item_events, _) in done]
            llm_calls = sum(calls for _, (_, calls) in done)
            if failed:
                counts["failed"] = len(failed)
        for item, item_events in per_item:
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
        mark_extracted = getattr(repo, "mark_extracted", None)
        if to_events is not None and mark_extracted is not None:
            # Items rejected by the pre-filter are marked too, so they are not considered again
            extracted_ids = [i.external_id for i in items if i.external_id not in failed | deferred]
            mark_extracted(source.id, extracted_ids, extraction.extractor_id())

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
