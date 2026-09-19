"""LondonAir (Imperial College ERG) hourly air quality index per monitoring site.

Each site reports a Daily Air Quality Index value (1-10, 0 = no data) per
pollutant. A site produces an event when its highest index is 4 ("Moderate")
or above.

The upstream id of an item is the site code plus the bulletin hour, e.g.
`BG1:2026-09-19T11`. The events upsert does not update `occurred_at` on
conflict, so an id of the site code alone would keep the `occurred_at` of the
first elevated bulletin and the event would decay to zero while the site is
still elevated. With the hour in the id, each hourly bulletin is a separate
event with its own `occurred_at` and the category half-life. The previous
hour's event is no longer in the fetch result and is marked ended by the
snapshot logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..models import (
    CATEGORY_DEFAULTS,
    SOURCE_TYPE_CONFIDENCE,
    Category,
    Event,
    RawItem,
    in_london,
)
from .base import external_ref

URL = "https://api.erg.ic.ac.uk/AirQuality/Hourly/MonitoringIndex/GroupName=London/Json"
SITE_URL = "https://www.londonair.org.uk/london/asp/publicdetails.asp?site={code}"

LOCAL_TZ = ZoneInfo("Europe/London")

# Index 1-3 is the "Low" band and produces no event.
MIN_INDEX = 4


class LondonAirSource:
    id = "london_air"
    snapshot = True

    def fetch(self, cursor: dict[str, Any]) -> tuple[list[RawItem], dict[str, Any]]:
        resp = httpx.get(URL, timeout=30)
        resp.raise_for_status()
        items = [
            RawItem(source_id=self.id, external_id=_external_id(p), payload=p)
            for p in flatten(resp.json())
        ]
        return items, cursor

    def to_event(self, raw: RawItem) -> Event | None:
        d = raw.payload
        index = d["max_index"]
        if index < MIN_INDEX:
            return None

        lng, lat = d["lon"], d["lat"]
        if not in_london(lng, lat):
            return None

        category = Category.AIR_QUALITY
        defaults = CATEGORY_DEFAULTS[category]
        confidence = SOURCE_TYPE_CONFIDENCE["official_feed"]
        elevated = ", ".join(
            f"{code} {i}" for code, i in sorted(d["species"].items()) if i >= MIN_INDEX
        )
        return Event(
            external_ref=external_ref(raw),
            category=category,
            title=f"Air quality {index} ({d['band']}, {d['max_species']}): {d['site_name']}"[:200],
            summary=f"Daily Air Quality Index by pollutant: {elevated}",
            geometry={"type": "Point", "coordinates": [lng, lat]},
            lng=lng,
            lat=lat,
            radius_m=defaults.radius_m,
            severity=severity(index),
            confidence=confidence,
            source_confidence={self.id: confidence},
            half_life_min=defaults.half_life_min,
            occurred_at=_parse_bulletin(d["bulletin_date"]),
            source_ids=[self.id],
            urls=[SITE_URL.format(code=d["site_code"])],
        )


def severity(index: int) -> float:
    """UK Daily Air Quality Index bands: 4-6 Moderate, 7-9 High, 10 Very High."""
    if index >= 10:
        return 0.8
    if index >= 7:
        return round(0.45 + 0.08 * (index - 7), 2)
    return round(0.2 + 0.05 * (index - 4), 2)


def flatten(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One payload per site with coordinates. `Site` and `Species` are each a
    dict when there is one entry and a list when there are several; an
    authority without sites has no `Site` key."""
    payloads = []
    for authority in _as_list(data.get("HourlyAirQualityIndex", {}).get("LocalAuthority")):
        for site in _as_list(authority.get("Site")):
            try:
                lat, lon = float(site["@Latitude"]), float(site["@Longitude"])
            except (KeyError, TypeError, ValueError):
                continue

            species: dict[str, int] = {}
            bands: dict[str, str] = {}
            for sp in _as_list(site.get("Species")):
                code = sp["@SpeciesCode"]
                species[code] = int(sp.get("@AirQualityIndex") or 0)
                bands[code] = sp.get("@AirQualityBand", "")
            max_species = max(species, key=species.__getitem__, default=None)
            payloads.append(
                {
                    "site_code": site["@SiteCode"],
                    "site_name": site.get("@SiteName", ""),
                    "lat": lat,
                    "lon": lon,
                    "bulletin_date": site["@BulletinDate"],
                    "max_index": species[max_species] if max_species else 0,
                    "max_species": max_species,
                    "band": bands[max_species] if max_species else "No data",
                    "species": species,
                }
            )
    return payloads


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _external_id(payload: dict[str, Any]) -> str:
    # "2026-09-19 11:00:00" -> "BG1:2026-09-19T11"
    hour = payload["bulletin_date"][:13].replace(" ", "T")
    return f"{payload['site_code']}:{hour}"


def _parse_bulletin(value: str) -> datetime:
    """Bulletin dates are naive Europe/London local times."""
    local = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)
    return local.astimezone(timezone.utc)
