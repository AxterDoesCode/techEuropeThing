"""The `ui` object of the chat response: scripted model (FunctionModel) and a fake platform API."""

import re

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from backend import api_chat, chat
from backend.geocode import GeoResult
from backend.scoring import _M_PER_DEG_LAT, _M_PER_DEG_LNG

LNG, LAT = -0.13, 51.525
E_ON, E_NEAR, E_FAR, E_LOW = (f"00000000-0000-4000-8000-00000000000{i}" for i in range(1, 5))


def at(east_m: float, north_m: float) -> list[float]:
    return [LNG + east_m / _M_PER_DEG_LNG, LAT + north_m / _M_PER_DEG_LAT]


def event(id_: str, title: str, east_m: float, north_m: float, radius_m: float, risk: float, **props) -> dict:
    lng, lat = at(east_m, north_m)
    return {"type": "Feature", "id": id_, "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"id": id_, "title": title, "category": "violent_crime", "subtype": "assault",
                           "state": "ongoing", "ongoing": True, "risk": risk, "severity": 0.7, "lng": lng, "lat": lat,
                           "radius_m": radius_m, "occurred_at": "2026-09-19T12:00:00Z", "source_ids": ["met_news"],
                           "urls": [f"https://news.example/{id_[-1]}"], "summary": "long text"} | props}


def route_part(north_m: float, length_m: float) -> dict:
    return {"geometry": {"type": "LineString", "coordinates": [at(0, 0), at(500, north_m), at(1000, 0)]},
            "length_m": length_m, "duration_min": 13.0, "mean_risk": 0.2, "max_risk": 0.4, "path_risk": 0.31,
            "lit_share": 0.9, "main_road_share": 0.4, "park_m": 0.0, "underpass_m": 0.0,
            "steps": [{"instruction": "Head east on Gower Street", "street": "Gower Street", "distance_m": 400},
                      {"instruction": "Continue", "street": None, "distance_m": 100},
                      {"instruction": "Turn left onto Euston Road", "street": "Euston Road", "distance_m": 300},
                      {"instruction": "Turn right onto Gower Street", "street": "Gower Street", "distance_m": 200},
                      {"instruction": "Arrive at destination", "street": None, "distance_m": 0.0}]}


# the safe route runs through (500, 0); the fast route bends 300 m north
ROUTE = {"fast": route_part(300, 1000.0), "safe": route_part(0, 1100.0), "alpha": 4.0, "night_multiplier": 1.3,
         "risk_reduction": 0.2, "extra_distance_m": 100.0, "attribution": "OSM"}
EVENTS = [event(E_ON, "Assault on Gower Street", 300, 20, 50, 0.4),
          event(E_NEAR, "Road closure on Euston Road", 700, -150, 100, 0.6),
          event(E_FAR, "Flood warning", 500, 900, 100, 0.9),
          event(E_LOW, "Protest at Russell Square", 900, 0, 50, 0.1)]
AREA = {"center": [LNG, LAT], "radius_m": 600, "generated_at": "2026-09-19T16:00:00+00:00",
        "risk": {"mean_score": 0.31, "max_score": 0.52, "mean_live": 0.05, "mean_baseline": 0.7,
                 "london_percentile": 0.93, "cells": 7},
        "crime": {"month": "2026-07", "period": "2025-09..2026-08", "method": "m", "recorded_crimes": 412,
                  "weighted": 190.5, "top_categories": {"violent-crime": 90}, "top_streets": []},
        "events": [event(E_NEAR, "Road closure on Euston Road", 700, -150, 100, 0.6, distance_m=716),
                   event(E_ON, "Assault on Gower Street", 300, 20, 50, 0.4, distance_m=301)]}
requests: list[httpx.Request] = []


def fake_api(request: httpx.Request) -> httpx.Response:
    requests.append(request)
    path = request.url.path
    if path == "/api/area":
        return httpx.Response(200, json=AREA)
    if path == "/api/route":
        return httpx.Response(200, json=ROUTE)
    if path == "/api/events":
        return httpx.Response(200, json={"type": "FeatureCollection", "features": EVENTS})
    if path == f"/api/events/{E_ON}":
        return httpx.Response(200, json=EVENTS[0])
    return httpx.Response(404, json={"detail": "not found"})


def deps() -> chat.ChatDeps:
    requests.clear()
    places = {"Bloomsbury garden": GeoResult(*at(0, 0), 50.0, "Bloomsbury Square Garden, London", "test"),
              "Euston station": GeoResult(*at(1000, 0), 50.0, "Euston Station, London", "test")}
    return chat.ChatDeps(api_base="https://platform.test", http=httpx.Client(transport=httpx.MockTransport(fake_api)),
                         geocoder=lambda q: places.get(q))


def scripted(calls: list[tuple[str, dict]], final, seen: dict | None = None):
    """Issues the tool calls one per step, then returns final(tool_results): a dict is sent
    as the structured output, a string as plain text."""

    def run(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if seen is not None:
            seen["prompt"] = messages[0].parts[-1].content
        if len(returns) < len(calls):
            name, args = calls[len(returns)]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        out = final(returns)
        if isinstance(out, str):
            return ModelResponse(parts=[TextPart(out)])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, out)])

    return FunctionModel(run)


def lat_lng(prefix: str, east_m: float, north_m: float) -> dict:
    lng, lat = at(east_m, north_m)
    return {f"{prefix}lat": lat, f"{prefix}lng": lng}


ROUTE_CALLS = [("find_place", {"query": "Bloomsbury garden"}), ("find_place", {"query": "Euston station"}),
               ("walking_route", lat_lng("from_", 0, 0) | lat_lng("to_", 1000, 0)
                | {"from_label": "Bloomsbury garden", "to_label": "Euston station"})]
ASK_ROUTE = [{"role": "user", "content": "plan a walk route from Bloomsbury garden to Euston station"}]
EMPTY_UI = {"intent": "other", "route": None, "route_labels": None, "area": None, "events": [],
            "highlight_event_ids": [], "focus": None}


def test_route_flow():
    seen = {}

    def final(returns):
        seen["route"] = returns[2].content
        return {"answer": "The lower-risk route is 100 m longer. Road closure on Euston Road is along it.",
                "intent": "route", "show_route": True, "focus": "route",
                "highlight_event_ids": [E_NEAR, "not-an-id", E_FAR, E_ON, E_NEAR]}

    d = deps()
    out = chat.answer(ASK_ROUTE, d, scripted(ROUTE_CALLS, final))
    ui = out["ui"]
    assert ui["intent"] == "route" and ui["focus"] == "route"
    assert ui["route"] == ROUTE
    assert ui["route_labels"] == {"origin": "Bloomsbury garden", "destination": "Euston station"}
    assert ui["area"] is None
    # E_FAR is 900 m from the line; the others are within radius_m + 60 m of it; highest risk first
    assert [f["id"] for f in ui["events"]] == [E_NEAR, E_ON, E_LOW]
    near = ui["events"][0]
    assert near["properties"]["relevance"] == {"distance_m": pytest.approx(150, abs=0.5),
                                               "along_m": pytest.approx(700, abs=0.5)}
    assert near["geometry"] == EVENTS[1]["geometry"] and near["properties"]["summary"] == "long text"
    assert "relevance" not in EVENTS[1]["properties"]
    # ids that are unknown, or known to the API but not along the route, are dropped; duplicates too
    assert ui["highlight_event_ids"] == [E_NEAR, E_ON]

    # the events request covers the safe route
    [events_request] = [r for r in requests if r.url.path == "/api/events"]
    w, s, e, n = (float(v) for v in events_request.url.params["bbox"].split(","))
    assert w < at(0, 0)[0] and e > at(1000, 0)[0] and s < LAT < n

    # what the model sees: figures, a summary of the steps, a compact event list; no geometry
    r = seen["route"]
    assert r["night_multiplier"] == 1.3 and r["extra_distance_m"] == 100.0
    assert "geometry" not in r["safe"] and r["safe"]["path_risk"] == 0.31 and r["safe"]["lit_share"] == 0.9
    assert r["safe"]["steps"] == {"count": 5, "streets": ["Gower Street", "Euston Road"]}
    assert r["events_along_safe_route_total"] == 3
    assert r["events_along_safe_route"][0] == {
        "id": E_NEAR, "title": "Road closure on Euston Road", "category": "violent_crime", "subtype": "assault",
        "state": "ongoing", "risk": 0.6, "distance_m": pytest.approx(150, abs=0.5),
        "along_m": pytest.approx(700, abs=0.5)}
    assert {"title": "Road closure on Euston Road", "url": "https://news.example/2"} in out["sources"]


def test_route_labels_fall_back_to_the_find_place_label():
    calls = ROUTE_CALLS[:2] + [("walking_route", lat_lng("from_", 0, 0) | lat_lng("to_", 1000, 300))]
    final = lambda r: {"answer": "A route.", "intent": "route", "show_route": True}  # noqa: E731
    ui = chat.answer(ASK_ROUTE, deps(), scripted(calls, final))["ui"]
    assert ui["route_labels"]["origin"] == "Bloomsbury Square Garden, London"
    assert re.fullmatch(r"51\.\d{4}, -0\.\d{4}", ui["route_labels"]["destination"])
    assert ui["focus"] == "route"  # focus omitted by the model: taken from the intent


def test_area_flow():
    seen = {}

    def final(returns):
        seen["area"] = returns[1].content
        return {"answer": "412 crimes were recorded (2025-09..2026-08). Assault on Gower Street is ongoing.",
                "intent": "area", "show_route": False, "focus": "area", "highlight_event_ids": [E_ON, E_LOW]}

    calls = [("find_place", {"query": "Euston station"}),
             ("area_report", lat_lng("", 0, 0) | {"radius_m": 600, "label": "  Bethnal\n Green " + "x" * 200})]
    out = chat.answer([{"role": "user", "content": "how safe is the Bethnal Green area"}], deps(), scripted(calls, final))
    ui = out["ui"]
    assert ui["intent"] == "area" and ui["focus"] == "area" and ui["route"] is None and ui["route_labels"] is None
    area = ui["area"]
    assert area["label"].startswith("Bethnal Green x") and len(area["label"]) == chat.MAX_LABEL_CHARS
    assert area["center"] == [LNG, LAT] and area["radius_m"] == 600
    w, s, e, n = area["bbox"]
    assert [w, s] == pytest.approx(at(-600, -600), abs=1e-5) and [e, n] == pytest.approx(at(600, 600), abs=1e-5)
    assert [f["id"] for f in ui["events"]] == [E_NEAR, E_ON]
    assert ui["events"][1]["properties"]["relevance"] == {"distance_m": 301.0, "along_m": None}
    assert ui["highlight_event_ids"] == [E_ON]  # E_LOW is not inside the area
    assert seen["area"]["events"][0]["id"] == E_NEAR and seen["area"]["events"][0]["state"] == "ongoing"


def test_plain_question_and_response_keys():
    final = lambda r: {"answer": "Call 999 in an emergency.", "intent": "other"}  # noqa: E731
    out = chat.answer([{"role": "user", "content": "hello"}], deps(), scripted([], final))
    # `answer`, `sources`, `places`, `tool_calls` are read by apps/ask; `ui` is additive
    assert list(out) == ["answer", "sources", "places", "tool_calls", "ui"]
    assert out["answer"] == "Call 999 in an emergency." and out["sources"] == [] and out["places"] == []
    assert out["ui"] == EMPTY_UI


def test_claims_without_tool_calls_are_repaired():
    final = lambda r: {"answer": "Here is your route.", "intent": "route", "show_route": True,  # noqa: E731
                       "focus": "route", "highlight_event_ids": [E_ON]}
    assert chat.answer(ASK_ROUTE, deps(), scripted([], final))["ui"] == EMPTY_UI
    final = lambda r: {"answer": "The area.", "intent": "area", "focus": "area"}  # noqa: E731
    assert chat.answer(ASK_ROUTE, deps(), scripted([], final))["ui"] == EMPTY_UI


def test_failed_route_call_attaches_nothing():
    def api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "origin is more than 300 m from the walking network"})

    d = chat.ChatDeps(api_base="https://platform.test", http=httpx.Client(transport=httpx.MockTransport(api)))
    final = lambda r: {"answer": r[0].content["error"], "intent": "route", "show_route": True}  # noqa: E731
    out = chat.answer(ASK_ROUTE, d, scripted(ROUTE_CALLS[2:], final))
    assert out["answer"].startswith("origin is more") and out["ui"] == EMPTY_UI


def test_events_api_failure_keeps_the_route():
    def api(request: httpx.Request) -> httpx.Response:
        return fake_api(request) if request.url.path == "/api/route" else httpx.Response(500, json={"detail": "db"})

    d = chat.ChatDeps(api_base="https://platform.test", http=httpx.Client(transport=httpx.MockTransport(api)))
    seen = {}

    def final(returns):
        seen["route"] = returns[0].content
        return {"answer": "A route.", "intent": "route", "show_route": True}

    ui = chat.answer(ASK_ROUTE, d, scripted(ROUTE_CALLS[2:], final))["ui"]
    assert ui["route"] == ROUTE and ui["events"] == []
    assert seen["route"]["events_along_safe_route"] is None and "events_error" in seen["route"]


def test_plain_text_reply_derives_the_ui_from_the_tools():
    out = chat.answer(ASK_ROUTE, deps(), scripted(ROUTE_CALLS, lambda r: "Longer by 100 m. Assault on Gower Street is on it."))
    ui = out["ui"]
    assert ui["intent"] == "route" and ui["focus"] == "route" and ui["route"] == ROUTE
    assert ui["highlight_event_ids"] == [E_ON]


def test_empty_answer_is_retried():
    attempts = []

    def final(returns):
        attempts.append(1)
        return {"answer": " " if len(attempts) == 1 else "Second attempt.", "intent": "other"}

    # the retry prompt is a tool return, so the script counts attempts itself
    def run(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, final(None))])

    assert chat.answer(ASK_ROUTE, deps(), FunctionModel(run))["answer"] == "Second attempt."
    assert len(attempts) == 2


def test_events_are_capped_and_highlighted_events_are_kept():
    d = deps()
    d.route, d.route_labels, d.last_map_tool = ROUTE, {"origin": "a", "destination": "b"}, "route"
    d.route_events = [chat._with_relevance(event(f"id{i}", f"t{i}", 0, 0, 50, i / 100), 0.0, 0.0) for i in range(60)]
    ui = chat.build_ui(chat.ChatOutput(answer="x", intent="route", show_route=True, highlight_event_ids=["id3"]), d)
    ids = [f["id"] for f in ui["events"]]
    assert len(ids) == chat.MAX_UI_EVENTS and ids[0] == "id59" and ids[-1] == "id3" and "id20" not in ids
    assert ui["highlight_event_ids"] == ["id3"]


def test_context_reaches_the_prompt():
    seen = {}
    context = {"center": [-0.0553, 51.5273], "bounds": [-0.07, 51.52, -0.04, 51.53], "zoom": 14.2,
               "selected_event_id": E_ON}
    chat.answer([{"role": "user", "content": "what is around here?"}], deps(),
                scripted([], lambda r: "x", seen), context=context)
    assert "centred on lat 51.52730, lng -0.05530 at zoom 14.2" in seen["prompt"]
    assert f"id {E_ON}, titled 'Assault on Gower Street'" in seen["prompt"]
    assert seen["prompt"].endswith("what is around here?")

    # an id that is not a UUID is not requested from the API and not shown to the model
    d = deps()
    chat.answer([{"role": "user", "content": "hi"}], d, scripted([], lambda r: "x", seen),
                context={"selected_event_id": "../agents"})
    assert seen["prompt"] == "hi" and requests == []


# The patterns with which apps/ask parses `tool_calls`. The strings must keep this format.
NUM = r"-?\d+(?:\.\d+)?"
TOOL_CALL_PATTERNS = {
    "area_report": rf"^(area_report|hotels_near)\(\s*{NUM}\s*,\s*{NUM}\s*,\s*{NUM}\s*\)$",
    "walking_route": rf"^walking_route\(\s*\(\s*{NUM}\s*,\s*{NUM}\s*\)\s*->\s*\(\s*{NUM}\s*,\s*{NUM}\s*\)\s*\)$",
    "find_place": r"^find_place\(([\s\S]*)\)$",
}


def test_tool_call_strings_keep_their_format():
    calls = ROUTE_CALLS + [("area_report", lat_lng("", 0, 0) | {"radius_m": 600, "label": "Bloomsbury"}),
                           ("hotels_near", lat_lng("", 0, 0))]
    out = chat.answer(ASK_ROUTE, deps(), scripted(calls, lambda r: "x"))
    lng, lat = at(1000, 0)
    assert out["tool_calls"] == [
        "find_place('Bloomsbury garden')", "find_place('Euston station')",
        f"walking_route(({LAT:.4f}, {LNG:.4f}) -> ({lat:.4f}, {lng:.4f}))",
        f"area_report({LAT:.4f}, {LNG:.4f}, 600)", f"hotels_near({LAT:.4f}, {LNG:.4f}, 1500)"]
    for call in out["tool_calls"]:
        name = call.split("(")[0]
        assert re.match(TOOL_CALL_PATTERNS["area_report" if name == "hotels_near" else name], call), call


def test_http_service_passes_the_context(monkeypatch):
    client = TestClient(api_chat.chat_app)
    monkeypatch.setenv("LLM_MODEL", "test")
    monkeypatch.setattr(api_chat, "_hits", {})
    got = {}

    def fake_answer(messages, context=None):
        got["context"] = context
        return {"answer": "ok", "sources": [], "places": [], "tool_calls": [], "ui": EMPTY_UI}

    monkeypatch.setattr(chat, "answer", fake_answer)
    body = {"messages": [{"role": "user", "content": "around here?"}]}
    assert client.post("/api/chat", json=body).json()["ui"] == EMPTY_UI and got["context"] is None

    context = {"center": [-0.05, 51.52], "bounds": [-0.07, 51.52, -0.04, 51.53], "zoom": 14, "unknown_key": 1}
    assert client.post("/api/chat", json=body | {"context": context}).status_code == 200
    assert got["context"] == chat.MapContext(center=(-0.05, 51.52), bounds=(-0.07, 51.52, -0.04, 51.53), zoom=14)
    assert client.post("/api/chat", json=body | {"context": {}}).status_code == 200
    assert client.post("/api/chat", json=body | {"context": {"center": [51.52, -200]}}).status_code == 422

    def boom(messages, context=None):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(chat, "answer", boom)
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == 502 and "secret" not in resp.text and "detail" in resp.json()
