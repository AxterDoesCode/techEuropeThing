"""Place text -> coordinates, restricted to Greater London.

Order: UK postcode (postcodes.io), then Nominatim bounded to the London bbox.
Nominatim's usage policy allows 1 request per second and requires a User-Agent.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

from .models import LONDON_BBOX, in_london
from .scoring import distance_m

USER_AGENT = "london-live-risk-map/0.1 (hackathon project)"
POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.I)
MIN_PRECISION_M = 100.0
MAX_PRECISION_M = 3000.0


@dataclass(frozen=True)
class GeoResult:
    lng: float
    lat: float
    # approximate radius of the matched place
    precision_m: float
    label: str
    provider: str


class GeocodeCache(Protocol):
    def geocode_get(self, query: str) -> dict[str, Any] | None: ...
    def geocode_put(self, query: str, result: dict[str, Any] | None) -> None: ...


_cache: dict[str, GeoResult | None] = {}
# Persistent cache (the repo). Without one, results are cached per process only.
_store: GeocodeCache | None = None


def use_cache(store: GeocodeCache | None) -> None:
    global _store
    _store = store
_nominatim_lock = threading.Lock()
_last_nominatim_call = 0.0


def geocode(place_text: str) -> GeoResult | None:
    key = " ".join(place_text.lower().split())
    if not key:
        return None
    if key in _cache:
        return _cache[key]
    if _store is not None and (row := _store.geocode_get(key)) is not None:
        hit = row["lng"] is not None
        _cache[key] = (
            GeoResult(row["lng"], row["lat"], row["precision_m"], row["label"], row["provider"])
            if hit
            else None
        )
        return _cache[key]
    result = _postcode(place_text) or _nominatim(place_text)
    _cache[key] = result
    if _store is not None:
        _store.geocode_put(key, asdict(result) if result else None)
    return result


def _postcode(text: str) -> GeoResult | None:
    m = POSTCODE_RE.search(text)
    if not m:
        return None
    resp = httpx.get(f"https://api.postcodes.io/postcodes/{m.group(1)}{m.group(2)}", timeout=15)
    if resp.status_code != 200:
        return None
    r = resp.json()["result"]
    if r["longitude"] is None or not in_london(r["longitude"], r["latitude"]):
        return None
    return GeoResult(r["longitude"], r["latitude"], MIN_PRECISION_M, r["postcode"], "postcodes.io")


def _nominatim(text: str) -> GeoResult | None:
    global _last_nominatim_call
    w, s, e, n = LONDON_BBOX
    with _nominatim_lock:
        time.sleep(max(0.0, 1.0 - (time.monotonic() - _last_nominatim_call)))
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": f"{text}, London",
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "gb",
                "viewbox": f"{w},{n},{e},{s}",
                "bounded": 1,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        _last_nominatim_call = time.monotonic()
    if resp.status_code != 200 or not resp.json():
        return None
    r = resp.json()[0]
    lng, lat = float(r["lon"]), float(r["lat"])
    if not in_london(lng, lat):
        return None
    south, north, west, east = (float(v) for v in r["boundingbox"])
    half_diagonal = distance_m(west, south, east, north) / 2
    precision = min(MAX_PRECISION_M, max(MIN_PRECISION_M, half_diagonal))
    return GeoResult(lng, lat, precision, r.get("display_name", text), "nominatim")
