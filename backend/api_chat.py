"""HTTP service for the chat agent. It runs as its own Modal function (not in the
Store container): an answer takes several LLM requests and tool calls."""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from . import chat, llm

chat_app = FastAPI(title="London Live Risk Map chat")
chat_app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Every answer spends LLM quota, so requests per client address are limited.
RATE_LIMIT = 20
RATE_WINDOW_S = 600
_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def _allow(client: str) -> bool:
    now = time.monotonic()
    with _lock:
        recent = [t for t in _hits.get(client, []) if now - t < RATE_WINDOW_S]
        allowed = len(recent) < RATE_LIMIT
        if allowed:
            recent.append(now)
        _hits[client] = recent
    return allowed


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class Context(BaseModel):
    """State of the client's map. Optional; unknown keys are ignored."""

    center: tuple[float, float] | None = Field(None, description="[lng, lat]")
    bounds: tuple[float, float, float, float] | None = Field(None, description="[west, south, east, north]")
    zoom: float | None = Field(None, ge=0, le=30)
    selected_event_id: str | None = Field(None, max_length=64)

    @field_validator("center")
    @classmethod
    def _lng_lat(cls, p: tuple[float, float] | None) -> tuple[float, float] | None:
        if p is not None and not (-180 <= p[0] <= 180 and -90 <= p[1] <= 90):
            raise ValueError("expected [lng, lat] in degrees")
        return p


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=30)
    context: Context | None = None


@chat_app.post("/api/chat")
def post_chat(body: ChatRequest, request: Request) -> dict[str, Any]:
    if body.messages[-1].role != "user":
        raise HTTPException(422, "the last message must be from the user")
    if not llm.is_configured():
        raise HTTPException(503, "chat is not available: LLM_MODEL is not set")
    client = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0]
    if not _allow(client):
        raise HTTPException(429, f"rate limit: {RATE_LIMIT} questions per {RATE_WINDOW_S // 60} minutes")
    try:
        messages = [m.model_dump() for m in body.messages]
        if body.context is None:
            return chat.answer(messages)
        return chat.answer(messages, context=chat.MapContext(**body.context.model_dump()))
    except Exception as exc:  # model errors, exceeded usage limits
        raise HTTPException(502, f"the assistant could not answer: {type(exc).__name__}") from exc
