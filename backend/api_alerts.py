"""GET /api/alerts. Include in the app with `web.include_router(api_alerts.router)`.

Official wide-area alerts that cover London: Met Office weather warnings and UK
Emergency Alerts (backend/alerts.py). They are shown as a notice in the client and
are not part of /api/events, /api/cells or routing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from . import db
from .alerts import LEVEL_RANK, SOURCE_LABELS, Level, covers_london
from .models import utcnow

router = APIRouter()

# An alert that has not started yet is returned when it starts within this time
UPCOMING_WINDOW = timedelta(hours=24)
# An alert without an end time (an Emergency Alert that is still being broadcast)
# ends only when a later poll no longer lists it. If polls have failed for this
# long, such an alert is no longer returned, because its state is unknown.
OPEN_ENDED_MAX_AGE = timedelta(hours=6)


def current_alerts(now: datetime, min_level: str = "amber") -> list[dict[str, Any]]:
    out = []
    for a in db.stored_alerts():
        if LEVEL_RANK[a["level"]] < LEVEL_RANK[min_level]:
            continue
        if not covers_london(a["source"], a["area_text"]):
            continue
        if a["ends_at"] is not None and a["ends_at"] <= now:
            continue
        if a["ends_at"] is None and a["fetched_at"] + OPEN_ENDED_MAX_AGE < now:
            continue
        if a["starts_at"] > now + UPCOMING_WINDOW:
            continue
        out.append(
            {
                "id": a["id"],
                "source": a["source"],
                "source_label": SOURCE_LABELS.get(a["source"], a["source"]),
                "level": a["level"],
                "hazard": a["hazard"],
                "headline": a["headline"],
                "url": a["url"],
                "starts_at": a["starts_at"],
                "ends_at": a["ends_at"],
                "active": a["starts_at"] <= now,
            }
        )
    out.sort(key=lambda a: (-LEVEL_RANK[a["level"]], a["starts_at"], a["id"]))
    return out


@router.get("/api/alerts")
def get_alerts(
    min_level: Level = Query("amber", description="lowest level returned: yellow, amber or red"),
) -> list[dict[str, Any]]:
    return current_alerts(utcnow(), min_level)
