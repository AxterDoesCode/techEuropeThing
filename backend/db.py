"""SQLite storage. All SQL lives here.

One process owns the database file: on Modal that is the single `Store`
container (see app.py), locally it is the dev server. The connection is shared
between threads, so every operation runs under one lock.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
from uuid import UUID, uuid4

import h3

from .models import ENDED_HALF_LIFE_MIN, CellScore, Event, RawItem, utcnow
from .scoring import MIN_EVENT_RISK

SCHEMA_PATH = Path(__file__).parent / "sql" / "schema.sql"

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


def connect(path: str | Path) -> None:
    """Open (and create if needed) the database file and apply the schema."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("pragma journal_mode = wal")
        _conn.execute("pragma foreign_keys = on")
        _conn.executescript(SCHEMA_PATH.read_text())
        _conn.commit()


def snapshot(dest: str | Path) -> None:
    """Write a consistent copy of the database to `dest` (atomic replace)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with _tx() as conn:
        copy = sqlite3.connect(tmp)
        conn.backup(copy)
        copy.close()
    os.replace(tmp, dest)


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    if _conn is None:
        raise RuntimeError("database not opened; call db.connect(path) first")
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except BaseException:
            _conn.rollback()
            raise


def _ts(dt: datetime | None) -> str | None:
    """Fixed-width UTC text, so string comparison equals time comparison."""
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _event_from_row(row: sqlite3.Row) -> Event:
    d = dict(row)
    for key in ("geometry", "source_confidence", "source_ids", "raw_item_ids", "urls"):
        d[key] = json.loads(d[key])
    for key in ("h3_r10", "h3_r9", "h3_r7", "created_at", "updated_at"):
        d.pop(key, None)
    return Event.model_validate(d)


def _event_params(ev: Event, now: str) -> dict[str, Any]:
    return {
        "id": str(ev.id or uuid4()),
        "external_ref": ev.external_ref,
        "category": ev.category.value,
        "title": ev.title,
        "summary": ev.summary,
        "geometry": json.dumps(ev.geometry, separators=(",", ":")),
        "lng": ev.lng,
        "lat": ev.lat,
        "radius_m": ev.radius_m,
        "h3_r10": h3.latlng_to_cell(ev.lat, ev.lng, 10),
        "h3_r9": h3.latlng_to_cell(ev.lat, ev.lng, 9),
        "h3_r7": h3.latlng_to_cell(ev.lat, ev.lng, 7),
        "severity": ev.severity,
        "confidence": ev.confidence,
        "source_confidence": json.dumps(ev.source_confidence),
        "half_life_min": ev.half_life_min,
        "occurred_at": _ts(ev.occurred_at),
        "expires_at": _ts(ev.expires_at),
        "ended_at": _ts(ev.ended_at),
        "source_ids": json.dumps(ev.source_ids),
        "raw_item_ids": json.dumps(ev.raw_item_ids),
        "urls": json.dumps(ev.urls),
        "now": now,
    }


# Columns whose change makes an upsert write (and bump updated_at)
_COMPARED = ("category", "title", "summary", "geometry", "lng", "lat", "radius_m", "severity", "expires_at")


class SqliteRepo:
    """Storage operations used by the polling pipeline and the rescore job."""

    def get_cursor(self, source_id: str) -> dict[str, Any]:
        with _tx() as conn:
            row = conn.execute("select cursor from sources where id = ?", [source_id]).fetchone()
        if row is None:
            raise KeyError(f"unknown source {source_id!r}")
        return json.loads(row["cursor"])

    def due_sources(self, now: datetime) -> list[str]:
        with _tx() as conn:
            rows = conn.execute(
                "select id, poll_interval_s, last_polled_at from sources where enabled"
            ).fetchall()
        due = []
        for r in rows:
            last = datetime.fromisoformat(r["last_polled_at"]) if r["last_polled_at"] else None
            if last is None or last + timedelta(seconds=r["poll_interval_s"]) <= now:
                due.append(r["id"])
        return due

    def upsert_raw_items(self, items: list[RawItem]) -> dict[str, int]:
        """Store items and return external_id -> raw_items.id. A row is rewritten
        only when its payload changed."""
        now = _ts(utcnow())
        ids: dict[str, int] = {}
        with _tx() as conn:
            for it in items:
                payload = json.dumps(it.payload, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(payload.encode()).hexdigest()
                row = conn.execute(
                    "select id, payload_hash from raw_items where source_id = ? and external_id = ?",
                    [it.source_id, it.external_id],
                ).fetchone()
                if row is None:
                    cur = conn.execute(
                        "insert into raw_items (source_id, external_id, fetched_at, payload, payload_hash)"
                        " values (?, ?, ?, ?, ?)",
                        [it.source_id, it.external_id, now, payload, digest],
                    )
                    ids[it.external_id] = cur.lastrowid
                else:
                    if row["payload_hash"] != digest:
                        conn.execute(
                            "update raw_items set payload = ?, payload_hash = ?, fetched_at = ? where id = ?",
                            [payload, digest, now, row["id"]],
                        )
                    ids[it.external_id] = row["id"]
        return ids

    def upsert_structured_events(self, events: list[Event]) -> int:
        """Insert or update by external_ref; returns the number inserted. An
        unchanged event is not written. An event that reappears upstream has its
        ended_at cleared."""
        now = _ts(utcnow())
        inserted = 0
        with _tx() as conn:
            for ev in events:
                params = _event_params(ev, now)
                row = conn.execute(
                    f"select id, ended_at, {', '.join(_COMPARED)} from events where external_ref = ?",
                    [ev.external_ref],
                ).fetchone()
                if row is None:
                    conn.execute(
                        """
                        insert into events (
                          id, external_ref, category, title, summary, geometry, lng, lat, radius_m,
                          h3_r10, h3_r9, h3_r7, severity, confidence, source_confidence,
                          half_life_min, occurred_at, expires_at, ended_at,
                          source_ids, raw_item_ids, urls, created_at, updated_at)
                        values (
                          :id, :external_ref, :category, :title, :summary, :geometry, :lng, :lat,
                          :radius_m, :h3_r10, :h3_r9, :h3_r7, :severity, :confidence,
                          :source_confidence, :half_life_min, :occurred_at, :expires_at, :ended_at,
                          :source_ids, :raw_item_ids, :urls, :now, :now)
                        """,
                        params,
                    )
                    inserted += 1
                elif row["ended_at"] is not None or any(row[c] != params[c] for c in _COMPARED):
                    conn.execute(
                        """
                        update events set category = :category, title = :title, summary = :summary,
                          geometry = :geometry, lng = :lng, lat = :lat, radius_m = :radius_m,
                          h3_r10 = :h3_r10, h3_r9 = :h3_r9, h3_r7 = :h3_r7, severity = :severity,
                          expires_at = :expires_at, ended_at = null, updated_at = :now
                        where external_ref = :external_ref
                        """,
                        params,
                    )
        return inserted

    def end_missing(self, source_id: str, seen_refs: Iterable[str], at: datetime) -> int:
        """Mark events of a snapshot source that were not in the latest fetch as ended."""
        seen = set(seen_refs)
        with _tx() as conn:
            rows = conn.execute(
                "select external_ref from events where external_ref like ? and ended_at is null",
                [f"{source_id}:%"],
            ).fetchall()
            missing = [r["external_ref"] for r in rows if r["external_ref"] not in seen]
            conn.executemany(
                "update events set ended_at = ?, updated_at = ? where external_ref = ?",
                [(_ts(at), _ts(at), ref) for ref in missing],
            )
        return len(missing)

    def start_run(self, source_id: str, started_at: datetime) -> int:
        with _tx() as conn:
            cur = conn.execute(
                "insert into agent_runs (source_id, started_at) values (?, ?)",
                [source_id, _ts(started_at)],
            )
            return cur.lastrowid

    def finish_run(
        self,
        run_id: int,
        source_id: str,
        finished_at: datetime,
        cursor: dict[str, Any] | None,
        counts: dict[str, int],
        error: str | None,
    ) -> None:
        with _tx() as conn:
            conn.execute(
                """
                update agent_runs set finished_at = ?, fetched = ?, inserted = ?, merged = ?,
                  ended = ?, llm_calls = ?, error = ?
                where id = ?
                """,
                [
                    _ts(finished_at),
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
                "update sources set last_polled_at = ?, last_status = ?, cursor = coalesce(?, cursor) where id = ?",
                [
                    _ts(finished_at),
                    "ok" if error is None else f"error: {error[:500]}",
                    json.dumps(cursor) if cursor is not None else None,
                    source_id,
                ],
            )

    def scoring_candidates(self, now: datetime) -> list[Event]:
        """Events that can still have risk above the scoring threshold. The SQL
        filter is a loose bound; event_risk applies the exact rule."""
        # severity * confidence <= 1, so risk < threshold after this many half-lives
        n = math.ceil(math.log2(1 / MIN_EVENT_RISK))
        ended_cutoff = _ts(now - timedelta(minutes=ENDED_HALF_LIFE_MIN * n))
        with _tx() as conn:
            rows = conn.execute(
                """
                select * from events
                where occurred_at <= :now
                  and (ended_at is null or ended_at >= :ended_cutoff)
                  and (expires_at is null or expires_at >= :ended_cutoff)
                  and (half_life_min is null
                       or julianday(:now) - julianday(occurred_at) <= half_life_min * :n / 1440.0)
                """,
                {"now": _ts(now), "ended_cutoff": ended_cutoff, "n": n},
            ).fetchall()
        return [_event_from_row(r) for r in rows]

    def baseline(self, res: int) -> dict[str, float]:
        with _tx() as conn:
            rows = conn.execute(
                "select h3, crime_rate from baseline_cells where res = ?", [res]
            ).fetchall()
        return {r["h3"]: r["crime_rate"] for r in rows}

    def replace_cell_scores(self, scores: list[CellScore], now: datetime) -> None:
        with _tx() as conn:
            conn.execute("delete from cell_scores")
            conn.executemany(
                "insert into cell_scores (h3, res, live, baseline, score, top_event_ids, updated_at)"
                " values (?, ?, ?, ?, ?, ?, ?)",
                [
                    (c.h3, c.res, c.live, c.baseline, c.score,
                     json.dumps([str(i) for i in c.top_event_ids]), _ts(now))
                    for c in scores
                ],
            )

    def replace_baseline(self, cells: dict[str, float], res: int) -> None:
        with _tx() as conn:
            conn.execute("delete from baseline_cells where res = ?", [res])
            conn.executemany(
                "insert into baseline_cells (h3, res, crime_rate) values (?, ?, ?)",
                [(cell, res, rate) for cell, rate in cells.items()],
            )

    def save_crime_points(self, month: str, payload: dict[str, Any]) -> None:
        with _tx() as conn:
            conn.execute(
                "insert or replace into crime_points (month, payload, created_at) values (?, ?, ?)",
                [month, json.dumps(payload, separators=(",", ":")), _ts(utcnow())],
            )

    def geocode_get(self, query: str) -> dict[str, Any] | None:
        """Cached geocoder result: None = not cached; a dict with lng None = cached miss."""
        with _tx() as conn:
            row = conn.execute("select * from geocode_cache where query = ?", [query]).fetchone()
        return dict(row) if row else None

    def geocode_put(self, query: str, result: dict[str, Any] | None) -> None:
        r = result or {}
        with _tx() as conn:
            conn.execute(
                "insert or replace into geocode_cache (query, lng, lat, precision_m, label, provider, created_at)"
                " values (?, ?, ?, ?, ?, ?, ?)",
                [query, r.get("lng"), r.get("lat"), r.get("precision_m"), r.get("label"),
                 r.get("provider"), _ts(utcnow())],
            )


# Read queries used by the API


def event_by_id(event_id: UUID) -> Event | None:
    with _tx() as conn:
        row = conn.execute("select * from events where id = ?", [str(event_id)]).fetchone()
    return _event_from_row(row) if row else None


def events_geojson(
    now: datetime,
    bbox: tuple[float, float, float, float] | None = None,
    since: datetime | None = None,
    category: str | None = None,
    limit: int = 2000,
) -> list[Event]:
    """bbox filters on the event centroid."""
    where = ["occurred_at <= :now"]
    params: dict[str, Any] = {"now": _ts(now), "limit": limit}
    if bbox:
        where.append("lng between :w and :e and lat between :s and :n")
        params |= dict(zip("wsen", bbox))
    if since:
        where.append("updated_at >= :since")
        params["since"] = _ts(since)
    if category:
        where.append("category = :category")
        params["category"] = category
    with _tx() as conn:
        rows = conn.execute(
            f"select * from events where {' and '.join(where)} order by updated_at desc limit :limit",
            params,
        ).fetchall()
    return [_event_from_row(r) for r in rows]


def cell_scores(
    res: int, min_score: float, bbox: tuple[float, float, float, float] | None = None
) -> list[CellScore]:
    with _tx() as conn:
        rows = conn.execute(
            "select h3, res, live, baseline, score, top_event_ids from cell_scores"
            " where res = ? and score >= ?",
            [res, min_score],
        ).fetchall()
    cells = [CellScore.model_validate(dict(r) | {"top_event_ids": json.loads(r["top_event_ids"])}) for r in rows]
    if bbox:
        w, s, e, n = bbox
        cells = [
            c for c in cells
            if (ll := h3.cell_to_latlng(c.h3)) and s <= ll[0] <= n and w <= ll[1] <= e
        ]
    return cells


def agent_status() -> list[dict[str, Any]]:
    hour_ago = _ts(utcnow() - timedelta(hours=1))
    with _tx() as conn:
        rows = conn.execute(
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
              from agent_runs where started_at >= ? group by source_id
            ) r on r.source_id = s.id
            order by s.id
            """,
            [hour_ago],
        ).fetchall()
    return [dict(r) | {"enabled": bool(r["enabled"])} for r in rows]


def latest_crime_points_json() -> str | None:
    """The stored JSON text, returned without parsing (about 3 MB)."""
    with _tx() as conn:
        row = conn.execute("select payload from crime_points order by month desc limit 1").fetchone()
    return row["payload"] if row else None
