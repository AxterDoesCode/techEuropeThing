"""GeoJSON representation of an event, shared by the REST and stream endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .models import Event
from .scoring import event_risk


def event_feature(ev: Event, now: datetime) -> dict[str, Any]:
    props = ev.model_dump(mode="json", exclude={"geometry", "source_confidence", "raw_item_ids"})
    props["risk"] = round(event_risk(ev, now), 4)
    return {"type": "Feature", "id": str(ev.id), "geometry": ev.geometry, "properties": props}
