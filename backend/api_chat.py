"""HTTP service for the chat agent. It runs as its own Modal function (not in the
Store container): an answer takes several LLM requests and tool calls."""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=30)


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
        return chat.answer([m.model_dump() for m in body.messages])
    except Exception as exc:  # model errors, exceeded usage limits
        raise HTTPException(502, f"the assistant could not answer: {type(exc).__name__}") from exc
