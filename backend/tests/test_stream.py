import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import api_stream, db
from backend.api_stream import Sent, change_frames, hello_frame, resume_point, sse_frame, stream_frames
from backend.db import SqliteRepo
from backend.models import Event, utcnow
from backend.pipeline import run_poll, run_rescore
from backend.tests.test_pipeline import FIXTURE, FixtureSource

EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)
ENDED_REF = "tfl_road:TIMS-206772"


@pytest.fixture
def repo(tmp_path):
    db.connect(tmp_path / "risk.sqlite")
    return SqliteRepo()


def _mark(changes) -> datetime:
    return datetime.fromisoformat(changes["mark"])


def test_changes_since_returns_new_rows_once(repo):
    run_poll(FixtureSource(FIXTURE), repo)
    first = db.changes_since(EPOCH)
    assert len(first["events"]) == len(FIXTURE) - 1
    assert set(first["event_updated_at"]) == {str(e.id) for e in first["events"]}
    assert [r["source_id"] for r in first["runs"]] == ["tfl_road"]
    assert set(first["runs"][0]) == {"id", "source_id", "finished_at", "fetched", "inserted", "ended", "error"}
    assert first["runs"][0]["inserted"] == len(FIXTURE) - 1
    assert first["cells_updated_at"] is None
    assert first["mark"] == first["runs"][0]["finished_at"]

    second = db.changes_since(_mark(first))
    assert second["events"] == [] and second["runs"] == [] and second["cells_updated_at"] is None
    assert second["mark"] == first["mark"] and not second["truncated"]


def test_changes_since_reports_ended_event_run_and_cells(repo):
    run_poll(FixtureSource(FIXTURE), repo)
    mark = _mark(db.changes_since(EPOCH))

    run_poll(FixtureSource([d for d in FIXTURE if d["id"] != "TIMS-206772"]), repo)
    run_rescore(repo)
    changes = db.changes_since(mark)
    assert [e.external_ref for e in changes["events"]] == [ENDED_REF]
    assert changes["events"][0].ended_at is not None
    assert len(changes["runs"]) == 1 and changes["runs"][0]["ended"] == 1
    assert changes["cells_updated_at"] is not None
    assert changes["mark"] == max(changes["cells_updated_at"], changes["runs"][0]["finished_at"])
    assert db.changes_since(_mark(changes))["cells_updated_at"] is None


def test_changes_since_limit_keeps_rows_with_equal_timestamp(repo):
    run_poll(FixtureSource(FIXTURE), repo)
    # one upsert batch writes one updated_at for all its rows
    changes = db.changes_since(EPOCH, limit=1)
    assert changes["truncated"] and len(changes["events"]) == len(FIXTURE) - 1
    assert changes["mark"] == max(changes["event_updated_at"].values())
    assert db.changes_since(_mark(changes))["events"] == []


def _event(**kw) -> Event:
    base = dict(
        id="00000000-0000-0000-0000-000000000001",
        external_ref="tfl_road:X",
        category="road_closure",
        title="A1 closed",
        geometry={"type": "Point", "coordinates": [-0.1, 51.5]},
        lng=-0.1,
        lat=51.5,
        radius_m=100,
        severity=0.5,
        confidence=0.8,
        occurred_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        source_ids=["tfl_road"],
        feed_managed=True,
        is_ongoing=True,
        last_confirmed_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    return Event.model_validate(base | kw)


def test_change_frames_exact_output():
    now = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    live = _event()
    # ended 50 min ago: 0.4 * 0.5 ** (50 / 15) is below MIN_EVENT_RISK
    ended = _event(id="00000000-0000-0000-0000-000000000002", ended_at=now - timedelta(minutes=50))
    # a one-off incident past the hard cap of its kind
    decayed = _event(
        id="00000000-0000-0000-0000-000000000003", category="other", feed_managed=False, is_ongoing=False,
        occurred_at=now - timedelta(hours=7),
    )
    # ended 5 min ago: still has a residual risk, so it stays on the map
    residual = _event(id="00000000-0000-0000-0000-000000000004", ended_at=now - timedelta(minutes=5))
    run = {"id": 7, "source_id": "tfl_road", "finished_at": "2026-01-01T13:00:00.000000+00:00",
           "fetched": 3, "inserted": 1, "ended": 1, "error": None}
    changes = {
        "events": [live, ended, decayed, residual],
        "event_updated_at": {
            str(e.id): "2026-01-01T12:59:59.000000+00:00" for e in (live, ended, decayed, residual)
        },
        "runs": [run],
        "cells_updated_at": "2026-01-01T12:59:58.000000+00:00",
        "truncated": False,
        "mark": "2026-01-01T13:00:00.000000+00:00",
    }
    mark = changes["mark"]
    sent = Sent()
    frames = change_frames(changes, now, sent, mark)

    feature = {
        "type": "Feature",
        "id": str(live.id),
        "geometry": {"type": "Point", "coordinates": [-0.1, 51.5]},
        "properties": {
            "id": str(live.id), "external_ref": "tfl_road:X", "category": "road_closure",
            "title": "A1 closed", "summary": None, "lng": -0.1, "lat": 51.5, "radius_m": 100.0,
            "severity": 0.5, "confidence": 0.8, "subtype": None,
            "occurred_at": "2026-01-01T12:00:00Z", "expires_at": None, "ended_at": None,
            "is_ongoing": True, "feed_managed": True, "last_confirmed_at": "2026-01-01T12:00:00Z",
            "source_ids": ["tfl_road"], "urls": [],
            "state": "ongoing", "ongoing": True, "half_life_min": None, "risk": 0.4,
        },
    }
    assert frames[0].startswith(f"event: event_upsert\nid: {mark}\ndata: ") and frames[0].endswith("\n\n")
    assert json.loads(frames[0].split("data: ", 1)[1]) == feature
    assert frames[0].count("\n") == 4  # the JSON is on one line
    upsert = json.loads(frames[3].split("data: ", 1)[1])
    assert frames[3].startswith("event: event_upsert\n") and upsert["id"] == str(residual.id)
    assert upsert["properties"]["state"] == "ended" and 0.05 < upsert["properties"]["risk"] < 0.4
    del frames[3]
    assert frames[1:] == [
        f'event: event_end\nid: {mark}\ndata: {{"id":"{ended.id}"}}\n\n',
        f'event: event_end\nid: {mark}\ndata: {{"id":"{decayed.id}"}}\n\n',
        f'event: cells_changed\nid: {mark}\ndata: {{"updated_at":"2026-01-01T12:59:58.000000+00:00"}}\n\n',
        f'event: agent_run\nid: {mark}\ndata: {{"id":7,"source_id":"tfl_road",'
        f'"finished_at":"2026-01-01T13:00:00.000000+00:00","fetched":3,"inserted":1,"ended":1,"error":null}}\n\n',
    ]
    # rows read again through the overlap are not sent twice
    assert change_frames(changes, now, sent, mark) == []


def test_hello_frame():
    now = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    mark = "2026-01-01T12:00:00.000000+00:00"
    assert hello_frame(now, mark) == (
        f'event: hello\nid: {mark}\ndata: {{"server_time":"2026-01-01T13:00:00+00:00","mark":"{mark}"}}\n\n'
    )
    assert sse_frame("x", {}, "m") == "event: x\nid: m\ndata: {}\n\n"


def test_resume_point_prefers_header_and_limits_range():
    now = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    header = "2026-01-01T12:58:00.000000+00:00"
    assert resume_point(header, "2026-01-01T12:50:00Z", now) == datetime.fromisoformat(header)
    # unescaped '+' arrives as a space
    assert resume_point(None, "2026-01-01T12:59:00.000000 00:00", now) == now - timedelta(minutes=1)
    assert resume_point("garbage", None, now) == now
    assert resume_point(None, "2020-01-01T00:00:00Z", now) == now - timedelta(seconds=api_stream.MAX_RESUME_S)
    assert resume_point(None, "2030-01-01T00:00:00Z", now) == now


def test_stream_frames_sends_hello_then_changes(repo):
    since = utcnow()
    run_poll(FixtureSource(FIXTURE), repo)

    async def never_disconnected() -> bool:
        return False

    async def first_two() -> list[str]:
        gen = stream_frames(since, never_disconnected)
        out = [await anext(gen), await anext(gen)]
        await gen.aclose()
        return out

    hello, batch = asyncio.run(first_two())
    assert hello.startswith(f"event: hello\nid: {db._ts(since)}\n")
    frames = batch.split("\n\n")[:-1]
    kinds = [f.split("\n")[0] for f in frames]
    assert kinds == ["event: event_upsert"] * (len(FIXTURE) - 1) + ["event: agent_run"]
    mark = db.changes_since(since)["mark"]
    assert all(f.split("\n")[1] == f"id: {mark}" for f in frames)


def test_stream_frames_stops_when_client_disconnects(repo):
    async def disconnected() -> bool:
        return True

    async def collect() -> list[str]:
        return [f async for f in stream_frames(utcnow(), disconnected)]

    assert len(asyncio.run(collect())) == 1


def test_http_stream_first_frame_and_headers(repo, monkeypatch):
    # the test client reads the whole body, so the stream is limited to the hello message
    monkeypatch.setattr(api_stream, "MAX_LIFETIME_S", 0.0)
    app = FastAPI()
    app.include_router(api_stream.router)
    client = TestClient(app)

    resp = client.get("/api/stream", headers={"Last-Event-ID": db._ts(utcnow() - timedelta(seconds=30))})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache" and resp.headers["x-accel-buffering"] == "no"
    lines = resp.text.split("\n")
    assert lines[0] == "event: hello"
    assert lines[1] == f"id: {json.loads(lines[2].removeprefix('data: '))['mark']}"
    assert resp.text.endswith("\n\n") and resp.text.count("\n\n") == 1

    assert client.get("/api/stream", params={"since": "yesterday"}).status_code == 422
