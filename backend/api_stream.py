"""GET /api/stream: server-sent events for live updates.

The database has no change notification, so each open stream reads
`db.changes_since` every POLL_S seconds. The endpoint is a coroutine and the
synchronous read runs in the threadpool only for the duration of the query, so an
idle stream holds no thread.

Message types (the `event:` field); every `data:` field is one line of JSON:
  hello          {"server_time", "mark"}     first message of every connection
  event_upsert   GeoJSON Feature, same shape as the items of /api/events
  event_end      {"id"}                      event ended, or its risk is below MIN_EVENT_RISK
  cells_changed  {"updated_at"}              cell_scores were rewritten
  agent_run      {"id", "source_id", "finished_at", "fetched", "inserted", "ended", "error"}
Every message carries `id: <mark>`, the high-water mark timestamp. A client resumes
with the `Last-Event-ID` header (sent by EventSource on its own reconnects) or
with `?since=<mark>`. A comment line `: ping` is sent after HEARTBEAT_S seconds
without output so that proxies keep the connection open.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import db
from .features import event_feature
from .models import utcnow
from .scoring import MIN_EVENT_RISK

POLL_S = 2.0
HEARTBEAT_S = 15.0
# A connection is closed by the server after this long. Modal limits the duration
# of a web request, and every open stream occupies one of the Store container's
# concurrent input slots (`@modal.concurrent(max_inputs=100)` in app.py) for as
# long as it is open, including streams whose client has gone away without a
# detectable disconnect. EventSource reconnects by itself and resumes from
# Last-Event-ID, so closing loses no messages.
MAX_LIFETIME_S = 600.0
# Each read starts this far before the mark. Timestamps are assigned by the writer
# before its transaction (on Modal in another container, before the RPC to the
# Store), so a row can be committed with a timestamp slightly older than a mark
# that was already sent. Rows read twice are dropped by `Sent`.
OVERLAP_S = 10.0
# A resume point older than this is moved forward; the client reloads /api/events
# after a long disconnection instead of replaying it.
MAX_RESUME_S = 900.0

HEARTBEAT_FRAME = ": ping\n\n"

router = APIRouter()


@dataclass
class Sent:
    """What one connection has already sent, to drop rows read again because of OVERLAP_S."""

    events: dict[str, str] = field(default_factory=dict)  # event id -> updated_at
    runs: set[int] = field(default_factory=set)
    cells: str = ""


def sse_frame(event: str, data: dict[str, Any], mark: str) -> str:
    return f"event: {event}\nid: {mark}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def hello_frame(now: datetime, mark: str) -> str:
    return sse_frame("hello", {"server_time": now.isoformat(), "mark": mark}, mark)


def change_frames(changes: dict[str, Any], now: datetime, sent: Sent, mark: str) -> list[str]:
    """SSE frames for one result of db.changes_since; records what was sent in `sent`."""
    frames: list[str] = []
    for ev in changes["events"]:
        event_id = str(ev.id)
        updated_at = changes["event_updated_at"][event_id]
        if sent.events.get(event_id) == updated_at:
            continue
        sent.events[event_id] = updated_at
        feature = event_feature(ev, now)
        if ev.ended_at is not None or feature["properties"]["risk"] < MIN_EVENT_RISK:
            frames.append(sse_frame("event_end", {"id": event_id}, mark))
        else:
            frames.append(sse_frame("event_upsert", feature, mark))
    cells_ts = changes["cells_updated_at"]
    if cells_ts is not None and cells_ts > sent.cells:
        sent.cells = cells_ts
        frames.append(sse_frame("cells_changed", {"updated_at": cells_ts}, mark))
    for run in changes["runs"]:
        if run["id"] in sent.runs:
            continue
        sent.runs.add(run["id"])
        frames.append(sse_frame("agent_run", run, mark))
    return frames


def resume_point(last_event_id: str | None, since: str | None, now: datetime) -> datetime:
    """Start of the stream: Last-Event-ID (newer than the URL on a reconnect), then
    ?since, then the current time; limited to [now - MAX_RESUME_S, now]."""
    start = now
    for text, strict in ((last_event_id, False), (since, True)):
        if not text:
            continue
        try:
            # '+' in an unescaped query string arrives as a space
            start = datetime.fromisoformat(text.strip().replace(" ", "+"))
        except ValueError:
            if strict:
                raise HTTPException(422, "since must be an ISO 8601 timestamp")
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        break
    return min(max(start, now - timedelta(seconds=MAX_RESUME_S)), now)


async def stream_frames(
    since: datetime, is_disconnected: Callable[[], Awaitable[bool]]
) -> AsyncIterator[str]:
    mark = db._ts(since) or ""
    sent = Sent()
    overlap = timedelta(seconds=OVERLAP_S)
    started = last_output = time.monotonic()
    yield hello_frame(utcnow(), mark)
    while time.monotonic() - started < MAX_LIFETIME_S:
        if await is_disconnected():
            return
        changes = await run_in_threadpool(db.changes_since, datetime.fromisoformat(mark) - overlap)
        mark = max(mark, changes["mark"])
        frames = change_frames(changes, utcnow(), sent, mark)
        if frames:
            yield "".join(frames)
            last_output = time.monotonic()
        elif time.monotonic() - last_output >= HEARTBEAT_S:
            yield HEARTBEAT_FRAME
            last_output = time.monotonic()
        if changes["truncated"]:
            # more rows are waiting; the overlap would read the same rows again
            overlap = timedelta(0)
            continue
        overlap = timedelta(seconds=OVERLAP_S)
        await anyio.sleep(POLL_S)


@router.get("/api/stream")
async def get_stream(request: Request, since: str | None = None) -> StreamingResponse:
    start = resume_point(request.headers.get("last-event-id"), since, utcnow())
    return StreamingResponse(
        stream_frames(start, request.is_disconnected),
        media_type="text/event-stream",
        # X-Accel-Buffering: nginx-style proxies must not buffer the stream
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
