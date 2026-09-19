"""Headline filter that runs before any extraction. Discards items that cannot
be a current, located incident, so the extractor only sees candidates."""

from __future__ import annotations

import re

# Reports about the justice process or the organisation, not about a new incident
_NOT_INCIDENT = re.compile(
    r"\b(sentenced|jailed|convicted|guilty|charged|court|trial|inquest|dismissed|misconduct|"
    r"hearing|pleaded|banned|statement from|commissioner|award|recruit)\w*\b",
    re.I,
)
_INCIDENT = re.compile(
    r"\b(stabb|shoot|shot|murder|attack|assault|rape|robber|collision|crash|fire|"
    r"explosion|acid|disorder|protest|riot|demonstrat|terror|bomb|blaze|dies|died|death|critical|injur|appeal|witness|"
    r"missing|evacuat|cordon|arrest)\w*\b",
    re.I,
)


def is_incident_candidate(title: str, description: str = "") -> bool:
    if _NOT_INCIDENT.search(title):
        return False
    return bool(_INCIDENT.search(f"{title} {description}"))
