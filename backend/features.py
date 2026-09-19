"""GeoJSON representation of an event, shared by the REST and stream endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import Event
from .scoring import event_risk, event_state


def event_feature(ev: Event, now: datetime) -> dict[str, Any]:
    props = ev.model_dump(mode="json", exclude={"geometry", "source_confidence", "raw_item_ids"})
    state = event_state(ev, now)
    props["state"] = state  # "upcoming" | "ongoing" | "ended"
    props["ongoing"] = state == "ongoing"
    # Kept for the web client until it reads the lifecycle fields: null for a
    # feed-managed event, otherwise the residual half-life of the timing table.
    props["half_life_min"] = ev.half_life_min
    props["risk"] = round(event_risk(ev, now), 4)
    return {"type": "Feature", "id": str(ev.id), "geometry": ev.geometry, "properties": props}
