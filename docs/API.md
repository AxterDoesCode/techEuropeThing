# Platform API for client applications

Two base URLs. CORS is open on both. All coordinates are `[lng, lat]` in JSON bodies and GeoJSON; query parameters are named `lat` and `lng`. Scope is Greater London (lng -0.5104..0.3340, lat 51.2868..51.6919); points outside return 422.

| Name | URL | Serves |
| :--- | :--- | :--- |
| `API_BASE` | `https://alexchau256--london-risk-store-api.modal.run` | everything except chat |
| `CHAT_BASE` | `https://alexchau256--london-risk-chat.modal.run` | `POST /api/chat` |

Interactive schema: `API_BASE/docs` (OpenAPI at `API_BASE/openapi.json`).

Risk values are in [0, 1]. They are modelled values (current events combined with a police-recorded-crime baseline), not probabilities. Client wording must report them as such: show the number, the comparison with London, recorded crime counts and current events. Do not label places "safe", "unsafe" or "dangerous".

## GET /api/area?lat=&lng=&radius_m=400

What the platform holds for a circle (radius 50–3000 m).

```json
{
  "center": [-0.1365, 51.5136], "radius_m": 400, "generated_at": "2026-09-19T16:50:00+00:00",
  "risk": {"mean_score": 0.41, "max_score": 0.88, "mean_live": 0.03, "mean_baseline": 0.92,
           "london_percentile": 0.99, "cells": 5},
  "crime": {"month": "2026-07", "period": "2025-09..2026-08", "method": "MPS recorded crime per LSOA, …", "recorded_crimes": 1840, "weighted": 905.2,
            "top_categories": {"other-theft": 420, "theft-from-the-person": 390},
            "top_streets": [{"street": "On or near Old Compton Street", "recorded_crimes": 113}]},
  "events": [ /* GeoJSON Features as in /api/events, plus properties.distance_m, highest risk first, max 25 */ ]
}
```

`london_percentile` = share of scored London cells with a lower score than this area's mean. `crime.top_categories` covers the three most frequent categories per street point, so it sums to slightly less than `recorded_crimes`. `crime.period` and `crime.method` say what the counts cover (both can be null); show the period next to any crime figure. The crime data has no time of day.

## GET /api/hotels?lat=&lng=&radius_m=1500&sort=safety|distance

OpenStreetMap hotels, hostels, guest houses and apartments (no prices, no availability). Max 60 returned; `total` is the full count. Returns 503 until the places job has run.

```json
{
  "center": [-0.1246, 51.5308], "radius_m": 1500, "total": 84,
  "hotels": [{
    "id": "osm:node/123", "kind": "hotel", "name": "Example Hotel", "lng": -0.12, "lat": 51.53,
    "details": {"subtype": "hotel", "stars": "4", "website": "https://…", "phone": "…", "address": "10 Example Street WC1X 9AA"},
    "distance_m": 420,
    "risk": {"mean_score": 0.22, "max_score": 0.31, "mean_live": 0.0, "mean_baseline": 0.55, "london_percentile": 0.81, "cells": 3},
    "nearest_station": {"name": "King's Cross St. Pancras", "lng": -0.1239, "lat": 51.5304, "distance_m": 380}
  }],
  "attribution": "Hotel and station data (c) OpenStreetMap contributors"
}
```

`risk` is the `/api/area` risk block for 300 m around the hotel. Every key in `details` is optional. `nearest_station` can be null. For the walk between station and hotel call `/api/route`.

## POST /api/route

Body: `{"origin": [lng, lat], "destination": [lng, lat], "alpha": 4}` (`alpha` 0–10 optional: 0 = shortest path, higher = avoid risk more).

```json
{
  "fast": {"geometry": {"type": "LineString", "coordinates": [[lng, lat], …]}, "length_m": 3097, "duration_min": 38.2, "mean_risk": 0.246, "max_risk": 0.61},
  "safe": { /* same fields */ },
  "risk_reduction": 0.23, "extra_distance_m": 240.2, "alpha": 4.0, "attribution": "…"
}
```

Errors: 422 when a point is more than 300 m from the walking network or outside the graph area (inner London at present; Greater London after the next graph build), 503 when no graph is loaded. Being added by the routing work, additive only: per route `steps: [{instruction, street|null, distance_m, duration_s, lit, risk, start:[lng,lat]}]`, `lit_share`, `main_road_share`, and a request field `depart_at` (ISO time) for a night weighting. Clients must work when these are absent.

## POST CHAT_BASE/api/chat

Body: `{"messages": [{"role": "user"|"assistant", "content": "…"}]}` — the whole conversation so far, last message from the user (max 30 messages, 2000 characters each). The server keeps no state.

```json
{"answer": "…", "sources": [{"title": "…", "url": "https://…"}],
 "places": [{"query": "Brixton", "found": true, "label": "Brixton, London…", "lat": 51.46, "lng": -0.11, "precision_m": 900}],
 "tool_calls": ["find_place('Brixton')", "area_report(51.4627, -0.1145, 500)"]}
```

An answer takes 5–25 s. Errors: 429 (20 questions per 10 minutes per address), 502 (model error), 503 (no model configured).

## Existing endpoints

- `GET /api/events?bbox=w,s,e,n&category=&active=true` — GeoJSON FeatureCollection. Properties: `id, category, title, summary, lng, lat, radius_m, severity, confidence, occurred_at, expires_at, ended_at, source_ids, urls, risk`. Further properties will be added (`state`, `ongoing`, `subtype`, `last_confirmed_at`); ignore unknown ones.
- `GET /api/stream` — server-sent events: `hello`, `event_upsert` (a Feature), `event_end` (`{id}`), `cells_changed`, `agent_run`. Connections close after 60 s; `EventSource` reconnects by itself.
- `GET /api/cells?res=9|7&bbox=&min_score=` — `[{h3, res, score, live, baseline, top_event_ids}]`.
- `GET /api/crime-points` — `{month, columns, rows: [[lng, lat, count, weighted, street, {category: n}]]}`, about 0.6 MB gzipped.
- `GET /api/agents` — status per data source.

Place search for a text box: the platform has no public geocoding endpoint. Use Photon (`https://photon.komoot.io/api/?q=…&bbox=-0.5104,51.2868,0.3340,51.6919&limit=6`, no key, GeoJSON features with `properties.name/street/city` and `geometry.coordinates`), debounced, one request per keystroke pause.
