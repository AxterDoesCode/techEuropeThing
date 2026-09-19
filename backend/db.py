"""Postgres access. All SQL lives here."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any, Iterable
from uuid import UUID

import h3
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .models import CellScore, Event, RawItem
from .scoring import MIN_EVENT_RISK

SCHEMA_PATH = Path(__file__).parent / "sql" / "schema.sql"

EVENT_COLUMNS = """
  id, external_ref, category, title, summary,
  ST_AsGeoJSON(geom)::jsonb as geometry, ST_X(centroid) as lng, ST_Y(centroid) as lat,
  radius_m, severity, confidence, source_confidence, half_life_min,
  occurred_at, expires_at, ended_at, source_ids, raw_item_ids, urls
"""


@cache
def pool() -> ConnectionPool:
    return ConnectionPool(
        os.environ["DATABASE_URL"],
        min_size=1,
        max_size=4,
        kwargs={"row_factory": dict_row},
        open=True,
    )


def apply_schema() -> None:
    with pool().connection() as conn:
        conn.execute(SCHEMA_PATH.read_text())


def _event_params(ev: Event) -> dict[str, Any]:
    return {
        "external_ref": ev.external_ref,
        "category": ev.category.value,
        "title": ev.title,
        "summary": ev.summary,
        "geometry": json.dumps(ev.geometry),
        "lng": ev.lng,
        "lat": ev.lat,
        "radius_m": ev.radius_m,
        "h3_r10": h3.latlng_to_cell(ev.lat, ev.lng, 10),
        "h3_r9": h3.latlng_to_cell(ev.lat, ev.lng, 9),
        "h3_r7": h3.latlng_to_cell(ev.lat, ev.lng, 7),
        "severity": ev.severity,
        "confidence": ev.confidence,
        "source_confidence": Jsonb(ev.source_confidence),
        "half_life_min": ev.half_life_min,
        "occurred_at": ev.occurred_at,
        "expires_at": ev.expires_at,
        "ended_at": ev.ended_at,
        "source_ids": ev.source_ids,
        "raw_item_ids": ev.raw_item_ids,
        "urls": ev.urls,
    }


class PgRepo:
    """Storage operations used by the polling pipeline and the rescore job."""

    def get_cursor(self, source_id: str) -> dict[str, Any]:
        with pool().connection() as conn:
            row = conn.execute("select cursor from sources where id = %s", [source_id]).fetchone()
        if row is None:
            raise KeyError(f"unknown source {source_id!r}")
        return row["cursor"]

    def due_sources(self, now: datetime) -> list[str]:
        with pool().connection() as conn:
            rows = conn.execute(
                """
                select id from sources
                where enabled and (last_polled_at is null
                  or last_polled_at + make_interval(secs => poll_interval_s) <= %s)
                """,
                [now],
            ).fetchall()
        return [r["id"] for r in rows]

    def upsert_raw_items(self, items: list[RawItem]) -> dict[str, int]:
        """Store items and return external_id -> raw_items.id. Payload is
        refreshed on conflict so the latest upstream state is kept."""
        ids: dict[str, int] = {}
        with pool().connection() as conn:
            for it in items:
                row = conn.execute(
                    """
                    insert into raw_items (source_id, external_id, payload, processed)
                    values (%s, %s, %s, true)
                    on conflict (source_id, external_id)
                      do update set payload = excluded.payload, fetched_at = now()
                    returning id
                    """,
                    [it.source_id, it.external_id, Jsonb(it.payload)],
                ).fetchone()
                ids[it.external_id] = row["id"]
        return ids

    def upsert_structured_event(self, ev: Event) -> bool:
        """Insert or update by external_ref. Returns True when a row was inserted.
        An event that reappears upstream has its ended_at cleared."""
        with pool().connection() as conn:
            row = conn.execute(
                """
                insert into events (
                  external_ref, category, title, summary, geom, centroid, radius_m,
                  h3_r10, h3_r9, h3_r7, severity, confidence, source_confidence,
                  half_life_min, occurred_at, expires_at, ended_at,
                  source_ids, raw_item_ids, urls)
                values (
                  %(external_ref)s, %(category)s, %(title)s, %(summary)s,
                  ST_SetSRID(ST_GeomFromGeoJSON(%(geometry)s), 4326),
                  ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), %(radius_m)s,
                  %(h3_r10)s, %(h3_r9)s, %(h3_r7)s, %(severity)s, %(confidence)s,
                  %(source_confidence)s, %(half_life_min)s, %(occurred_at)s,
                  %(expires_at)s, %(ended_at)s, %(source_ids)s, %(raw_item_ids)s, %(urls)s)
                on conflict (external_ref) do update set
                  category = excluded.category, title = excluded.title,
                  summary = excluded.summary, geom = excluded.geom,
                  centroid = excluded.centroid, radius_m = excluded.radius_m,
                  h3_r10 = excluded.h3_r10, h3_r9 = excluded.h3_r9, h3_r7 = excluded.h3_r7,
                  severity = excluded.severity, expires_at = excluded.expires_at,
                  ended_at = null, updated_at = now()
                where (events.title, events.summary, events.severity, events.expires_at,
                       events.ended_at, events.radius_m, ST_AsBinary(events.geom))
                  is distinct from
                      (excluded.title, excluded.summary, excluded.severity, excluded.expires_at,
                       null::timestamptz, excluded.radius_m, ST_AsBinary(excluded.geom))
                returning (xmax = 0) as inserted
                """,
                _event_params(ev),
            ).fetchone()
        return bool(row and row["inserted"])

    def end_missing(self, source_id: str, seen_refs: Iterable[str], at: datetime) -> int:
        """Mark events of a snapshot source that were not in the latest fetch as ended."""
        with pool().connection() as conn:
            cur = conn.execute(
                """
                update events set ended_at = %s, updated_at = now()
                where external_ref like %s and ended_at is null
                  and not (external_ref = any(%s))
                """,
                [at, f"{source_id}:%", list(seen_refs)],
            )
            return cur.rowcount

    def start_run(self, source_id: str, started_at: datetime) -> int:
        with pool().connection() as conn:
            row = conn.execute(
                "insert into agent_runs (source_id, started_at) values (%s, %s) returning id",
                [source_id, started_at],
            ).fetchone()
        return row["id"]

    def finish_run(
        self,
        run_id: int,
        source_id: str,
        finished_at: datetime,
        cursor: dict[str, Any] | None,
        counts: dict[str, int],
        error: str | None,
    ) -> None:
        with pool().connection() as conn:
            conn.execute(
                """
                update agent_runs set finished_at = %s, fetched = %s, inserted = %s,
                  merged = %s, ended = %s, llm_calls = %s, error = %s
                where id = %s
                """,
                [
                    finished_at,
                    counts.get("fetched", 0),
                    counts.get("inserted", 0),
                    counts.get("merged", 0),
                    counts.get("ended", 0),
                    counts.get("llm_calls", 0),
                    error,
                    run_id,
                ],
            )
            conn.execute(
                """
                update sources set last_polled_at = %s, last_status = %s,
                  cursor = coalesce(%s, cursor)
                where id = %s
                """,
                [
                    finished_at,
                    "ok" if error is None else f"error: {error[:500]}",
                    Jsonb(cursor) if cursor is not None else None,
                    source_id,
                ],
            )

    def scoring_candidates(self, now: datetime) -> list[Event]:
        """Events that can still have risk above the scoring threshold. The SQL
        filter is a loose bound; event_risk applies the exact rule."""
        with pool().connection() as conn:
            rows = conn.execute(
                f"""
                select {EVENT_COLUMNS} from events
                where occurred_at <= %(now)s
                  and (half_life_min is null
                       or occurred_at >= %(now)s - make_interval(mins => half_life_min * %(n)s))
                  and (least(ended_at, expires_at) is null
                       or least(ended_at, expires_at) >= %(now)s - make_interval(mins => 30 * %(n)s))
                """,
                # severity * confidence <= 1, so risk < threshold after this many half-lives
                {"now": now, "n": _half_lives_to_threshold()},
            ).fetchall()
        return [Event.model_validate(r) for r in rows]

    def baseline(self, res: int) -> dict[str, float]:
        with pool().connection() as conn:
            rows = conn.execute(
                "select h3, crime_rate from baseline_cells where res = %s", [res]
            ).fetchall()
        return {r["h3"]: r["crime_rate"] for r in rows}

    def replace_cell_scores(self, scores: list[CellScore], now: datetime) -> None:
        """Write the new scores and delete rows for cells that no longer have one."""
        with pool().connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                insert into cell_scores (h3, res, live, baseline, score, top_event_ids, updated_at)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (h3, res) do update set
                  live = excluded.live, baseline = excluded.baseline, score = excluded.score,
                  top_event_ids = excluded.top_event_ids, updated_at = excluded.updated_at
                """,
                [
                    (c.h3, c.res, c.live, c.baseline, c.score, c.top_event_ids, now)
                    for c in scores
                ],
            )
            cur.execute("delete from cell_scores where updated_at < %s", [now])


def _half_lives_to_threshold() -> float:
    return math.ceil(math.log2(1 / MIN_EVENT_RISK))


def event_by_id(event_id: UUID) -> Event | None:
    with pool().connection() as conn:
        row = conn.execute(
            f"select {EVENT_COLUMNS} from events where id = %s", [event_id]
        ).fetchone()
    return Event.model_validate(row) if row else None


def events_geojson(
    now: datetime,
    bbox: tuple[float, float, float, float] | None = None,
    since: datetime | None = None,
    category: str | None = None,
    limit: int = 2000,
) -> list[Event]:
    where = ["occurred_at <= %(now)s"]
    params: dict[str, Any] = {"now": now, "limit": limit}
    if bbox:
        where.append("geom && ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326)")
        params |= dict(zip("wsen", bbox))
    if since:
        where.append("updated_at >= %(since)s")
        params["since"] = since
    if category:
        where.append("category = %(category)s")
        params["category"] = category
    with pool().connection() as conn:
        rows = conn.execute(
            f"""
            select {EVENT_COLUMNS} from events where {' and '.join(where)}
            order by updated_at desc limit %(limit)s
            """,
            params,
        ).fetchall()
    return [Event.model_validate(r) for r in rows]


def cell_scores(
    res: int, min_score: float, bbox: tuple[float, float, float, float] | None = None
) -> list[CellScore]:
    with pool().connection() as conn:
        rows = conn.execute(
            """
            select h3, res, live, baseline, score, top_event_ids from cell_scores
            where res = %s and score >= %s
            """,
            [res, min_score],
        ).fetchall()
    cells = [CellScore.model_validate(r) for r in rows]
    if bbox:
        w, s, e, n = bbox
        cells = [
            c for c in cells
            if (ll := h3.cell_to_latlng(c.h3)) and s <= ll[0] <= n and w <= ll[1] <= e
        ]
    return cells


def agent_status() -> list[dict[str, Any]]:
    with pool().connection() as conn:
        return conn.execute(
            """
            select s.id, s.kind, s.enabled, s.poll_interval_s, s.last_polled_at, s.last_status,
              coalesce(r.runs, 0) as runs_last_hour,
              coalesce(r.fetched, 0) as fetched_last_hour,
              coalesce(r.inserted, 0) as inserted_last_hour,
              coalesce(r.errors, 0) as errors_last_hour
            from sources s
            left join (
              select source_id, count(*) as runs, sum(fetched) as fetched,
                sum(inserted) as inserted, count(error) as errors
              from agent_runs where started_at >= now() - interval '1 hour'
              group by source_id
            ) r on r.source_id = s.id
            order by s.id
            """
        ).fetchall()
