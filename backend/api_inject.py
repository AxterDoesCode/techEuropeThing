"""POST /api/inject: turn free text into events through the extraction path.

Requires the header X-Inject-Token to equal the INJECT_TOKEN environment
variable. The endpoint is disabled (503) while INJECT_TOKEN is unset.
"""

from __future__ import annotations

import hmac
import os
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .db import SqliteRepo
from .extraction import extract_events
from .located import Article
from .models import utcnow

SOURCE_ID = "manual"
SOURCE_TYPE = "manual"

router = APIRouter()


class InjectRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    source_label: str | None = Field(default=None, max_length=100)


# Declared without `async`: FastAPI runs it in a worker thread, which the
# synchronous LLM run and geocoder need.
@router.post("/api/inject")
def inject(body: InjectRequest, x_inject_token: str | None = Header(default=None)) -> dict[str, Any]:
    expected = os.environ.get("INJECT_TOKEN")
    if not expected:
        raise HTTPException(503, "inject is disabled: INJECT_TOKEN is not set")
    if x_inject_token is None or not hmac.compare_digest(x_inject_token.encode(), expected.encode()):
        raise HTTPException(401, "invalid X-Inject-Token")

    text = body.text.strip()
    lines = text.splitlines() or [""]
    article = Article(
        title=lines[0][:200],
        description="\n".join(lines[1:]).strip(),
        url=None,
        published_at=utcnow(),
        source_id=body.source_label or SOURCE_ID,
        # every injection is a new item, so repeating a text creates a new event
        guid=uuid4().hex,
    )
    repo = SqliteRepo()
    lookup = repo if getattr(repo, "find_merge_candidates", None) else None
    events, llm_calls = extract_events(article, SOURCE_ID, SOURCE_TYPE, lookup)
    for ev in events:
        ev.id = uuid4()
    inserted = repo.upsert_structured_events(events)
    return {
        "inserted": inserted,
        "llm_calls": llm_calls,
        "events": [ev.model_dump(mode="json") for ev in events],
    }
