"""Hotels and rail stations in Greater London from OpenStreetMap (Overpass API).

Loaded by a batch job, not per request: one Overpass query per kind covers the
whole bounding box. Data (c) OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from . import db
from .models import LONDON_BBOX, in_london, utcnow

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
USER_AGENT = "london-live-risk-map/0.1 (hackathon project)"

QUERIES = {
    "hotel": 'nwr["tourism"~"^(hotel|hostel|guest_house|apartment)$"]["name"]',
    "station": 'nwr["railway"="station"]["name"]',
}


def overpass_query(kind: str) -> str:
    w, s, e, n = LONDON_BBOX
    return f"[out:json][timeout:120];{QUERIES[kind]}({s},{w},{n},{e});out center tags;"


def fetch(kind: str) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for url in OVERPASS_URLS:
        try:
            resp = httpx.post(url, data={"data": overpass_query(kind)}, headers={"User-Agent": USER_AGENT}, timeout=180)
            resp.raise_for_status()
            return parse(resp.json(), kind)
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
    raise RuntimeError(f"all Overpass servers failed for {kind}: {last_error!r}")


def parse(data: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """Overpass elements -> place dicts. Ways and relations carry `center`."""
    places = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        pos = el if "lat" in el else el.get("center")
        name = tags.get("name")
        if not pos or not name or not in_london(pos["lon"], pos["lat"]):
            continue
        address = " ".join(
            v for v in (tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:postcode")) if v
        )
        details = {
            "subtype": tags.get("tourism") or tags.get("station") or tags.get("railway"),
            "stars": tags.get("stars"),
            "website": tags.get("website") or tags.get("contact:website"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "address": address or None,
            "network": tags.get("network"),
        }
        places.append(
            {
                "id": f"osm:{el['type']}/{el['id']}",
                "kind": kind,
                "name": name,
                "lng": pos["lon"],
                "lat": pos["lat"],
                "details": {k: v for k, v in details.items() if v},
            }
        )
    return places


def replace(kind: str, places: list[dict[str, Any]]) -> int:
    """Replace all stored places of one kind."""
    now = utcnow().isoformat()
    with db._tx() as conn:
        conn.execute("delete from places where kind = ?", [kind])
        conn.executemany(
            "insert or replace into places (id, kind, name, lng, lat, details, updated_at) values (?, ?, ?, ?, ?, ?, ?)",
            [(p["id"], kind, p["name"], p["lng"], p["lat"], json.dumps(p["details"]), now) for p in places],
        )
    return len(places)


def within(kind: str, bbox: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    w, s, e, n = bbox
    with db._tx() as conn:
        rows = conn.execute(
            "select id, kind, name, lng, lat, details from places"
            " where kind = ? and lng between ? and ? and lat between ? and ?",
            [kind, w, e, s, n],
        ).fetchall()
    return [dict(r) | {"details": json.loads(r["details"])} for r in rows]


def count(kind: str) -> int:
    with db._tx() as conn:
        return conn.execute("select count(*) from places where kind = ?", [kind]).fetchone()[0]
