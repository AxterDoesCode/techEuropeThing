"""Chat agent with a scripted model (FunctionModel) and a fake platform API."""

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from backend import api_chat, chat
from backend.geocode import GeoResult

AREA = {
    "center": [-0.1145, 51.4627], "radius_m": 500, "generated_at": "2026-09-19T16:00:00+00:00",
    "risk": {"mean_score": 0.31, "max_score": 0.52, "mean_live": 0.05, "mean_baseline": 0.7,
             "london_percentile": 0.93, "cells": 7},
    "crime": {"month": "2026-07", "recorded_crimes": 412, "weighted": 190.5,
              "top_categories": {"violent-crime": 90}, "top_streets": []},
    "events": [{"type": "Feature", "id": "e1", "geometry": {"type": "Point", "coordinates": [-0.11, 51.46]},
                "properties": {"title": "Assault on Brixton Road", "category": "violent_crime", "risk": 0.4,
                               "severity": 0.7, "occurred_at": "2026-09-19T12:00:00Z", "source_ids": ["met_news"],
                               "distance_m": 120, "urls": ["https://news.met.police.uk/x"], "summary": "long text"}}],
}
ROUTE = {"fast": {"length_m": 3000, "duration_min": 37, "mean_risk": 0.25, "max_risk": 0.5,
                  "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
         "safe": {"length_m": 3250, "duration_min": 40, "mean_risk": 0.19, "max_risk": 0.4,
                  "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}},
         "risk_reduction": 0.24, "extra_distance_m": 250, "alpha": 4.0}


def fake_api(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/area":
        if float(request.url.params["lat"]) > 52:
            return httpx.Response(422, json={"detail": "point is outside Greater London"})
        return httpx.Response(200, json=AREA)
    if request.url.path == "/api/route":
        return httpx.Response(200, json=ROUTE)
    return httpx.Response(404, json={"detail": "not found"})


def deps(geocoder=lambda q: GeoResult(-0.1145, 51.4627, 300.0, "Brixton, London", "test")) -> chat.ChatDeps:
    return chat.ChatDeps(api_base="https://platform.test", http=httpx.Client(transport=httpx.MockTransport(fake_api)),
                         geocoder=geocoder)


def scripted(calls: list[tuple[str, dict]], final):
    """Issues the tool calls one per step, then answers with final(tool_results)."""

    def run(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if len(returns) < len(calls):
            name, args = calls[len(returns)]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        return ModelResponse(parts=[TextPart(final(returns))])

    return FunctionModel(run)


def test_area_question_uses_tools_and_returns_sources():
    seen = {}

    def final(returns):
        seen["area"] = returns[1].content
        return "412 crimes were recorded within 500 m in July 2026."

    model = scripted([("find_place", {"query": "Brixton"}), ("area_report", {"lat": 51.4627, "lng": -0.1145})], final)
    d = deps()
    out = chat.answer([{"role": "user", "content": "What is happening around Brixton?"}], d, model)
    assert out["answer"].startswith("412 crimes")
    assert out["places"][0]["label"] == "Brixton, London"
    assert out["sources"] == [{"title": "Assault on Brixton Road", "url": "https://news.met.police.uk/x"}]
    assert out["tool_calls"] == ["find_place('Brixton')", "area_report(51.4627, -0.1145, 500)"]
    # events are trimmed to the fields the model needs; the long summary and geometry are dropped
    assert set(seen["area"]["events"][0]) == {"title", "category", "risk", "severity", "occurred_at", "source_ids",
                                              "distance_m", "urls"}


def test_route_tool_drops_geometry_and_api_errors_reach_the_model():
    got = {}

    def final(returns):
        got["route"], got["area"] = returns[0].content, returns[1].content
        return "done"

    model = scripted([("walking_route", {"from_lat": 51.53, "from_lng": -0.12, "to_lat": 51.51, "to_lng": -0.13}),
                      ("area_report", {"lat": 53.48, "lng": -2.24})], final)
    chat.answer([{"role": "user", "content": "route?"}], deps(), model)
    assert "geometry" not in got["route"]["fast"] and got["route"]["risk_reduction"] == 0.24
    assert got["area"]["error"] == "point is outside Greater London"


def test_unknown_place():
    got = {}
    model = scripted([("find_place", {"query": "Nowhere"})], lambda r: got.setdefault("r", r[0].content) and "Which place?")
    out = chat.answer([{"role": "user", "content": "Nowhere?"}], deps(geocoder=lambda q: None), model)
    assert got["r"] == {"found": False} and out["places"] == []


def test_build_prompt_keeps_recent_turns():
    assert chat.build_prompt([{"role": "user", "content": "hi"}]) == "hi"
    msgs = [{"role": "user", "content": "Brixton?"}, {"role": "assistant", "content": "412 crimes."},
            {"role": "user", "content": "and at night?"}]
    prompt = chat.build_prompt(msgs)
    assert "User: Brixton?" in prompt and "Assistant: 412 crimes." in prompt and prompt.endswith("and at night?")
    many = [{"role": "user", "content": f"m{i}"} for i in range(20)]
    assert "m11" not in chat.build_prompt(many) and "m12" in chat.build_prompt(many)


def test_http_service(monkeypatch):
    client = TestClient(api_chat.chat_app)
    body = {"messages": [{"role": "user", "content": "Brixton?"}]}
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert client.post("/api/chat", json=body).status_code == 503

    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(api_chat, "_hits", {})
    monkeypatch.setattr(chat, "answer", lambda messages: {"answer": "ok", "sources": [], "places": [], "tool_calls": []})
    assert client.post("/api/chat", json=body).json()["answer"] == "ok"
    assert client.post("/api/chat", json={"messages": [{"role": "assistant", "content": "x"}]}).status_code == 422
    assert client.post("/api/chat", json={"messages": []}).status_code == 422

    monkeypatch.setattr(api_chat, "RATE_LIMIT", 2)
    monkeypatch.setattr(api_chat, "_hits", {})
    codes = [client.post("/api/chat", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]

    def boom(messages):
        raise RuntimeError("quota")

    monkeypatch.setattr(api_chat, "_hits", {})
    monkeypatch.setattr(chat, "answer", boom)
    assert client.post("/api/chat", json=body).status_code == 502
