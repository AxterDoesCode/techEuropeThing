# London Live Risk Map — Build Specification

Replaces `HANDOFF_SPEC.md`. Scope: Greater London only. Hackathon proof of concept that runs on real data end to end; no hardcoded incidents, scores, or routes.

## 1. Product

A fleet of polling agents continuously writes geo-located events (crime, disorder, fires, transport disruption, flooding, road closures) into one central database. Every event is keyed by a geometry and an H3 cell. A scoring job turns events into a per-cell risk value that decays over time. A web client renders a 3D globe, focused on London, with risk cells highlighted and individual events inspectable. Phase 2 adds walking routes that weight street segments by the same cell risk.

Priority order:

1. Ingestion agents + database (the system is only credible if this is real)
2. Globe visualization with live updates
3. Agent fleet status panel
4. Risk-weighted routing (phase 2)

## 2. Decisions already made

| Topic | Decision |
| :--- | :--- |
| Region | Greater London, bbox `-0.5104, 51.2868, 0.3340, 51.6919` |
| Agent runtime | Modal (scheduled functions + `asgi_app` for the API) |
| Database | SQLite, hosted on Modal (decided 2026-09-19: no external database service). One `Store` container (`max_containers=1`) owns the file, serves the API, runs rescoring and executes all storage calls from the pollers. It works on container-local disk and writes a snapshot to a Modal Volume every 30 s and on shutdown, restoring it on start. At most 30 s of writes are lost on a crash, and the next polls re-create them. No PostGIS: spatial work is done in Python (shapely, H3), which is sufficient at hundreds of events |
| Spatial key | H3. Events stored at resolution 10, scores aggregated at resolutions 9 (street level, ~175 m edge) and 7 (city level, ~1.2 km edge). H3 indexes computed in Python with `h3`, stored as `text` |
| LLM | Provider-agnostic. Use `pydantic-ai`; model selected by env var, e.g. `LLM_MODEL=anthropic:claude-haiku-4-5`, `google-gla:gemini-2.5-flash`, `openai:gpt-...`. Output type is the Pydantic event model |
| Globe | deck.gl 9.4 on MapLibre GL JS **5.x** with `projection: globe`, `MapboxOverlay({interleaved: true})`. Verified 2026-09-19: hexagons, pitch and extrusion render correctly on the globe. MapLibre 6 must not be used: it removed `map.transform`, which deck.gl 9.4 reads, and every frame throws. Projection is `globe` below zoom 7 and `mercator` from zoom 7, switched in code: deck.gl's `HeatmapLayer` draws nothing under globe, and deck.gl rejects MapLibre's interpolated projection type. deck.gl `IconLayer` did not render in interleaved mode in testing; markers use `ScatterplotLayer`. Keyless basemaps: OpenFreeMap `dark` and `positron` (light mode) |
| Backend | One backend: FastAPI in Python. No Express server |
| Frontend | React + TypeScript + Vite |

## 3. Data sources

Checked on 2026-09-19 from this machine.

| Source | Endpoint | Status | Freshness | Needs LLM | Use |
| :--- | :--- | :--- | :--- | :--- | :--- |
| police.uk street crime | `data.police.uk/api/crimes-street/all-crime?lat=&lng=` (also `poly=`) | 200, no key | Monthly, latest month is 2026-07 | No | Baseline layer: 12 months of crime counts per H3 cell. Not a live signal |
| TfL road disruptions | `api.tfl.gov.uk/Road/all/Disruption` | 200, no key | Real time | No | Closures, works, collisions. Has `point`, `severity`, `category`, free-text `comments` |
| TfL line status / stop disruptions | `api.tfl.gov.uk/Line/Mode/tube,overground,dlr,elizabeth-line/Status`, `/StopPoint/Mode/{mode}/Disruption` | 200, no key | Real time | No | Station closures, severe delays. Geometry from StopPoint lat/lon |
| Environment Agency floods | `environment.data.gov.uk/flood-monitoring/id/floods?lat=51.5&long=-0.12&dist=30` | 200, no key | Real time | No | Flood warnings; flood area polygons available via the linked `floodArea` resource |
| Open-Meteo | `api.open-meteo.com/v1/forecast?...&current=` | 200, no key | 15 min | No | Wind gusts, heavy rain as a city-wide modifier |
| BBC London RSS | `feeds.bbci.co.uk/news/england/london/rss.xml` | 200 | Minutes to hours | Yes | Unstructured incident reports; requires extraction + geocoding |
| GDELT doc API | `api.gdeltproject.org/api/v2/doc/doc?...` | 429 when called back to back; limit is 1 request per 5 s | 15 min | Yes | Wider news coverage. Poll at most once per minute |
| Met Police news | `news.met.police.uk/rss/current_news/66871` (advertised in the newsroom page's `<link rel=alternate>`) | 200 | Hours; ~20 items, a few per day | Yes (rule-based fallback implemented) | Official incident statements and appeals. Most items are court outcomes and are rejected by the pre-filter |

Not yet checked, worth adding if time allows: London Fire Brigade incident data (London Datastore), TfL JamCam locations (CCTV coverage proxy), OSM `lit=yes/no` tags (street lighting, from the same OSM extract used for routing), additional local RSS (Evening Standard, MyLondon).

Considered and removed: LondonAir air quality index (built, then removed on 2026-09-19: air quality does not change a walking route, so it is not a relevant risk signal here).

Dropped from the Gemini spec: X/Twitter geo posts (no usable access), satellite night-light data (VIIRS resolution is ~500 m, useless per street), "Police CAD" (no public dispatch feed exists for London).

TfL allows anonymous calls at a low rate; register a free `app_key` to avoid throttling.

## 4. Architecture

```
Modal class Store (single container): SQLite file, HTTP API, rescoring, Store.call for storage RPC
   snapshot to Modal Volume `london-risk-db` every 30 s

Modal cron: dispatcher (every 1 min)
   asks Store which sources are due -> spawn poll_source(source_id) for each

poll_source(source_id)                      [Modal function, one container per call]
   fetch -> upsert into raw_items (dedupe on source_id + external_id/content hash)
   structured source:   map fields directly -> Event
   unstructured source: LLM extraction -> Event | None, then geocode
   -> merge_or_insert(Event) into events
   -> write a row to agent_runs (started, finished, fetched, new, errors)

Modal cron: rescore (every 1 min)
   recompute cell_scores for cells touched by active events; expire dead events

FastAPI (modal.asgi_app)
   GET /api/cells, /api/events, /api/agents, /api/stream (SSE), POST /api/inject, POST /api/route

React client
   MapLibre globe + deck.gl layers, polls or subscribes to /api/stream
```

A single dispatcher cron is used instead of one cron per source because Modal's free plan caps the number of scheduled functions (5, as far as I know — verify). It also makes poll intervals a database value instead of a deploy-time constant.

### Agent types

1. **Structured pollers** — TfL road (`tfl_road`), TfL station disruptions (`tfl_transit`), EA floods (`ea_floods`), police.uk (one-off `backfill_police`). Deterministic field mapping. No LLM. Open-Meteo is not built. A snapshot source ends events that leave its feed; an empty fetch ends nothing unless the source sets `empty_is_valid` (floods: no warnings is the normal state).
2. **Extraction agents** — RSS, GDELT, manual inject. Implemented so far: `met_news` with the code pre-filter, the geocoder, and a rule-based extractor (`extract_rules.py`: keyword category/severity, place from headline patterns, one incident per item) that is used while no LLM is configured. Met statements are published hours after the incident, so `met_news` events use a 24 h half-life instead of the category default. A tool-using LLM agent (`pydantic-ai`) with output type `list[ExtractedEvent]`; one article can describe zero, one or several incidents.

### Extraction agent

Pre-filter in code, before any LLM call: headline keywords and feed categories discard items that cannot be incidents. Only candidates reach the agent. Limit of 8 tool calls per article.

| Tool | Purpose |
| :--- | :--- |
| `fetch_article(url)` | RSS items carry only a headline and a short description; the location is usually in the body. Returns extracted main text, truncated to ~6k characters |
| `geocode(place_text)` | Returns up to 3 candidates `{place_id, label, precision_m, kind}`. The agent can retry with a different phrasing |
| `find_similar_events(category, place_id, occurred_at)` | Returns nearby recent events so the agent can set `existing_event_id` for follow-up reporting instead of creating a duplicate |
| `web_search(query)` | Stretch. Corroboration for high-severity single-source events |

Guardrails enforced in code, not by the prompt:

- Coordinates are taken only from the `place_id` of a `geocode` result produced in the same run. The model never outputs lat/lng.
- Results outside the London bbox are rejected inside the tool.
- Confidence = source-type confidence adjusted by geocode precision; never set by the model.
- An event with no resolved `place_id` is discarded.
- Every run records its tool calls and token counts in `agent_runs`.

### Geocoding

Order inside the `geocode` tool: (1) UK postcode in text -> `api.postcodes.io` (free, no key); (2) station names -> TfL StopPoint search; (3) otherwise Nominatim or Photon restricted to the London bbox, max 1 request/s, results cached in a `geocode_cache` table. Reject results outside the bbox. If only a borough is resolved, store the borough polygon centroid with a large radius and low confidence.

## 5. Database schema

`backend/sql/schema.sql` (SQLite) is the reference. Tables: `sources`, `raw_items` (payload rewritten only when its hash changes), `events`, `baseline_cells`, `crime_points`, `cell_scores`, `agent_runs`, `geocode_cache` (hits and misses).

Conventions: timestamps are ISO 8601 UTC text in one fixed-width format, so text comparison orders them; lists and objects are JSON text; geometry is GeoJSON text with `lng`/`lat` centroid columns for bbox filters; event ids are UUID text generated in Python. `events.external_ref` (`<source_id>:<upstream id>`) is the upsert identity. An upsert writes (and bumps `updated_at`) only when a compared field changed or the event had ended.

Process model: `db.connect(path)` opens one shared connection; every operation runs under one lock. `SqliteRepo` holds the pipeline's storage operations; pollers in other Modal containers reach it through `RemoteRepo`, which forwards each call to `Store.call`. `run_poll` makes 6 storage calls per poll (events are written in one batch).

Local development uses the same code without Modal: `python -m backend.local` (poll loop, rescoring and the API on port 8000, database at `data/risk.sqlite`).

## 6. Pydantic models (`backend/models.py`)

- `Category` enum: `violent_crime`, `property_crime`, `disorder`, `fire`, `road_closure`, `transit_disruption`, `flood`, `weather`, `other`.
- `ExtractedEvent` (LLM output, returned as a list): `category`, `title` (<=120 chars), `summary`, `place_id` (from a `geocode` tool result), `place_text`, `severity` 0–1, `occurred_at | None`, `is_ongoing`, `existing_event_id | None`. No coordinates, no confidence; those are assigned by code.
- `Event` (DB row): as in the table above.
- All datetimes timezone-aware UTC (`datetime.now(timezone.utc)`), not `utcnow()`.

Per-category defaults, overridable per event:

| Category | half_life_min | radius_m | Source-type confidence |
| :--- | :--- | :--- | :--- |
| violent_crime | 180 | 250 | official 0.9, news 0.7 |
| disorder | 90 | 300 | |
| fire | 120 | 200 | |
| road_closure / transit_disruption | no decay while upstream lists it; 30 after it disappears | from geometry | 0.95 |
| flood | no decay while warning active | polygon | 0.95 |

## 7. Scoring

All values are in [0, 1].

Event risk at time t:

```
r_i(t) = severity_i * confidence_i * 0.5 ^ ((t - occurred_at_i) / half_life_i)
```

For events with an active upstream state (closures, flood warnings), the decay term is 1 until `ended_at` (or `expires_at`) passes; after that a 30-minute half-life applies.

Contribution of event i to cell c, with d = distance from the event geometry to the cell centre minus the cell inradius, floored at 0 (so a point event gives weight 1 to the cell containing it):

```
w_i(c) = max(0, 1 - d / radius_i)
```

Live cell score, bounded and order-independent:

```
live(c) = 1 - Π_i (1 - r_i(t) * w_i(c))
score(c) = 1 - (1 - live(c)) * (1 - k * baseline(c))        k = 0.4
```

Events with `r_i < 0.05` are excluded from scoring (kept in the table for history). Resolution-7 value = 0.5 × max(children) + 0.5 × mean over all 49 children. A plain mean makes one high-risk street invisible at city zoom; the max term keeps it visible and the mean term separates areas with many affected cells from areas with one.

### Deduplication and merge

A new event merges into an existing one when: same category group, centroids within `max(radius)` of each other, and `occurred_at` within 3 hours. On merge: union `source_ids`, `urls`, `raw_item_ids`; `confidence = 1 - Π(1 - c_j)` over distinct sources; keep the higher severity and the more precise geometry. For structured sources the upstream id is the identity and merge is an upsert.

## 8. API

| Method | Path | Description |
| :--- | :--- | :--- |
| GET | `/api/cells?res=9&bbox=w,s,e,n&min_score=0.05` | `[{h3, score, live, baseline, top_event_ids}]` |
| GET | `/api/events?bbox=&since=&category=&active=true` | GeoJSON FeatureCollection; properties include current `risk` |
| GET | `/api/events/{id}` | Full event with sources and URLs |
| GET | `/api/agents` | Per source: enabled, interval, last run, last status, counts for the last hour |
| GET | `/api/stream` | SSE: `event_upsert`, `event_end`, `cells_changed`, `agent_run`. Implemented by polling `updated_at` every 2 s server-side. Client falls back to polling if Modal closes long connections |
| POST | `/api/inject` | `{text, source_label}` -> runs the unstructured pipeline on submitted text. For demos; events created this way carry `source_ids=['manual']` |
| POST | `/api/route` | Phase 2. `{origin, destination, alpha}` -> `{fast, safe}` each with GeoJSON LineString, length, duration, mean and max risk |

One response shape per endpoint, generated from the Pydantic models. Coordinates in API responses are always GeoJSON order `[lng, lat]`.

## 9. Frontend

- Initial view: whole globe, then an animated camera move to London (pitch 50°). Light/dark toggle (persisted in localStorage, defaults to the system preference) switches UI colours and the basemap style.
- Layers (deck.gl, interleaved with MapLibre):
  - `HeatmapLayer` — Met Police recorded crime (police.uk, latest month), weighted by category relevance to personal safety. Toggle in the Layers panel.
  - `GeoJsonLayer` — event geometry: affected road segments as lines (TfL `streets[].segments[].lineString`), flood areas as polygons. Colour = current risk, line width = severity.
  - `ScatterplotLayer` (metres) — affected radius for events that only have a point.
  - `ScatterplotLayer` (pixels) — one clickable marker per event, coloured by category, drawn without depth testing so buildings do not hide it.
  - MapLibre `fill-extrusion` buildings from the vector basemap.
  - `PathLayer` — routes (phase 2).
- The H3 cell scores are still computed server-side (`cell_scores`, used by routing) but are no longer drawn. `/api/cells` remains available.
- Clicking a marker or line opens a MapLibre popup at the event (details, sources, links); `closeOnClick` is disabled because the selecting click would also close it. Clicking empty map closes the popup and copies the clicked position into the coordinate fields.
- Panels: position (cursor longitude/latitude on hover, editable fields + Go to move the map), layers (crime toggle, colour scale), event feed (sorted by risk, click flies to the event and opens its popup), agent fleet (hidden until `/api/agents` has data).
- Not built yet: SSE, time slider, category filters.

## 10. Phase 2: routing

- Graph: OSMnx `network_type="walk"` for inner London (roughly zones 1–2: bbox `-0.26, 51.45, 0.02, 51.57`), built once offline, stored in a Modal Volume as a pickled/GraphML file, loaded at container start. Each edge is precomputed with the list of res-9 H3 cells it passes through and `lit` from OSM tags.
- Edge cost, strictly positive so Dijkstra/A* remain valid:

```
cost(e) = length(e) * (1 + alpha * risk(e)) * (1 - beta * lit(e))
risk(e) = max score of the cells e passes through
alpha in [0, 10] (user slider), beta = 0.15, lit(e) in {0, 1}
```

- `fast` = alpha 0, beta 0. `safe` = user alpha. Return both with real computed metrics. Cell scores are read from `cell_scores` per request (one query for the route bbox), so routes change as events arrive.

## 11. Repository layout

```
backend/
  app.py            Modal app: image, secrets, dispatcher cron, rescore cron, asgi mount
  api.py            FastAPI routes
  models.py         Pydantic models, category defaults
  db.py             SQLite connection, SqliteRepo, read queries, snapshot
  local.py          run the whole backend on one machine without Modal
  scoring.py        decay, cell aggregation, merge logic (pure functions, unit tested)
  pipeline.py       run_poll / run_rescore against a Repo protocol (PgRepo in db.py, in-memory repo in tests)
  tools/            export_sample.py: API-shaped JSON from live feeds, no database needed
  geocode.py
  llm.py            pydantic-ai extraction agent: prompt, tools, guardrails, model from env
  prefilter.py      headline/category filter that runs before the agent
  sources/          one module per source: fetch() -> list[RawItem], to_event(raw) -> Event | None
  routing.py        phase 2
  sql/schema.sql
  tests/
web/
  src/ (App, map/, panels/, api.ts, types generated from OpenAPI)
SPEC.md
```

Optional Modal secret `london-risk`: `TFL_APP_KEY`, `LLM_MODEL` and the matching provider key. Deploy with `LONDON_RISK_SECRET=1` to attach it. No database credentials exist.

## 12. Build order

1. Database schema. `scoring.py` with unit tests (decay, merge, cell aggregation).
2. TfL road disruptions poller writing real rows, run locally with `modal run`. This is the first end-to-end path.
3. FastAPI `/api/events` and `/api/cells`; rescore job.
4. Frontend globe with hexagon and event layers reading the live API.
5. Remaining structured pollers (TfL lines/stops, EA floods). Dispatcher cron. `/api/agents` and the fleet panel.
6. police.uk baseline backfill (one-off job, 12 months, tiled over the bbox with `poly=`).
7. Extraction agent path: pre-filter, geocode tool, `fetch_article`, `find_similar_events`, BBC RSS source, merge. Then GDELT and the Met feed. `/api/inject`.
8. SSE, time slider, visual polish.
9. Phase 2 routing.
10. Stretch: corroboration agent, LFB data, lighting.

Working demo exists after step 4; every later step adds data or features without restructuring.

## 13. Known risks

- deck.gl + MapLibre globe projection compatibility: check first, fallback listed in section 2.
- Geocoding accuracy for news text is the main source of wrong events. Mitigation: low confidence and large radius for imprecise matches, and discard when no place resolves.
- Live violent-crime signal for London is thin because no real-time police feed exists; most real-time volume will be transport, roads, floods. News-derived events fill part of the gap. The baseline layer carries the crime picture.
- Verified on Modal 2026-09-19: one class serving the ASGI API and RPC methods from a single container, `volume.commit()` from a background thread, snapshot restore across a redeploy, two crons. Still to verify: SSE connection duration.
- Nominatim usage policy (1 req/s, identify with a User-Agent). Cache all results.
