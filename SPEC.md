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
| LLM | Gemini (decided 2026-09-19), through `pydantic-ai`: `LLM_MODEL=google:gemini-3.8-flash` and `GOOGLE_API_KEY` in the Modal secret `london-risk`. The provider stays a configuration value; no code depends on Gemini. The agents run on Modal: one `extract_item` container per news item |
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
| Met Police news | `news.met.police.uk/rss/current_news/66871` (advertised in the newsroom page's `<link rel=alternate>`) | 200 | Hours; ~20 items, a few per day | Yes (rule-based fallback implemented) | Official incident statements and appeals. Most items are court outcomes and are rejected by the pre-filter |

Not yet checked, worth adding if time allows: London Fire Brigade incident data (London Datastore), TfL JamCam locations (CCTV coverage proxy), OSM `lit=yes/no` tags (street lighting, from the same OSM extract used for routing), additional local RSS (Evening Standard, MyLondon).

Considered and removed: GDELT doc API (dropped 2026-09-19 before being built: it only indexes articles after outlets publish them, so it is slower than polling the same outlets' RSS directly; London incident news comes from a small known set of outlets; rate limit of 1 request per 5 s); LondonAir air quality index (built, then removed on 2026-09-19: air quality does not change a walking route, so it is not a relevant risk signal here).

Dropped 2026-09-19: GDELT. It only discovers articles (no text, city-level locations), so each hit still needs the extraction agent; the direct feeds (`met_news`, `bbc_london`, `standard_london`, `mylondon`) cover the same outlets sooner.

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
   GET /api/cells, /api/events, /api/agents, /api/stream (SSE), POST /api/route

React client
   MapLibre globe + deck.gl layers, polls or subscribes to /api/stream
```

A single dispatcher cron is used instead of one cron per source because Modal's free plan caps the number of scheduled functions (5, as far as I know — verify). It also makes poll intervals a database value instead of a deploy-time constant.

### Agent types

1. **Structured pollers** — TfL road (`tfl_road`), TfL station disruptions (`tfl_transit`), police.uk (one-off `backfill_police`). Deterministic field mapping. No LLM. Open-Meteo is not built. A snapshot source ends events that leave its feed; an empty fetch ends nothing unless the source sets `empty_is_valid` (floods: no warnings is the normal state).
2. **Extraction agents** — RSS news feeds, later social sources. Implemented so far: `met_news` with the code pre-filter, the geocoder, and a rule-based extractor (`extract_rules.py`: keyword category/severity, place from headline patterns, one incident per item) that is used while no LLM is configured. Met statements are published hours after the incident, so `met_news` events use a 24 h half-life instead of the category default. A tool-using LLM agent (`pydantic-ai`) with output type `list[ExtractedEvent]`; one article can describe zero, one or several incidents.

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

### Extraction on Modal

`poll_source` fetches a feed, keeps the items that pass the headline pre-filter and are not yet extracted, and maps them over `extract_item`: one container per item, at most `EXTRACT_CONCURRENCY` (4) at a time, which bounds the request rate against the Gemini quota (up to 8 requests per item). `raw_items.extracted_with` records the extractor and payload hash, so an item is extracted once; an edited article or a change of extractor (rules to Gemini) makes it pending again. A failed item stays pending and is retried on the next poll. Sources with `requires_llm` never fall back to the keyword rules. All geocoding goes through `geocode_place`, a single container, so the Nominatim limit of 1 request per second holds across extraction containers.

### Geocoding

Order inside the `geocode` tool: (1) UK postcode in text -> `api.postcodes.io` (free, no key); (2) station names -> TfL StopPoint search; (3) otherwise Nominatim or Photon restricted to the London bbox, max 1 request/s, results cached in a `geocode_cache` table. Reject results outside the bbox. If only a borough is resolved, store the borough polygon centroid with a large radius and low confidence.

## 5. Database schema

`backend/sql/schema.sql` (SQLite) is the reference. Tables: `sources`, `raw_items` (payload rewritten only when its hash changes), `events`, `event_refs`, `baseline_cells`, `crime_points`, `cell_scores`, `agent_runs`, `geocode_cache` (hits and misses).

Conventions: timestamps are ISO 8601 UTC text in one fixed-width format, so text comparison orders them; lists and objects are JSON text; geometry is GeoJSON text with `lng`/`lat` centroid columns for bbox filters; event ids are UUID text generated in Python. `events.external_ref` (`<source_id>:<upstream id>`) is the first ref of a row; `event_refs(external_ref, event_id)` maps every ref that created or was merged into an event and is the upsert lookup (backfilled from `events.external_ref` on connect for older database files). An upsert writes (and bumps `updated_at`) only when a compared field changed or the event had ended.

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

Which events may merge is explicit: `Event.mergeable` (not stored, not serialised) is set True by unstructured sources (`met_news`, `bbc_london`, manual). `should_merge` requires at least one mergeable event, so two events from structured feeds never merge. Storage (`SqliteRepo.upsert_events`, returns `{"inserted", "merged"}`; `upsert_structured_events` delegates to it and returns the inserted count) handles each event in this order:

1. `external_ref` found in `event_refs`: update the mapped row, written only when something changed. A row made from one report is updated as a structured upsert. A merged row (several sources or several refs) only has its summary filled when empty, `urls`/`raw_item_ids` unioned, severity raised and geometry replaced by a more precise one; confidence is not touched, so re-polling a source does not raise it.
2. Otherwise, if the event has `merge_into` (set by the extraction agent) and that event exists in the same category group, merge into it without the distance and time tests. Otherwise, if the event is mergeable, take `find_merge_candidates(category, lng, lat, occurred_at, radius_m)` filtered by `should_merge` and merge into the closest (distance, then time). The merged row keeps its id, title and first `external_ref`; the new ref is added to `event_refs`. `run_poll` records `merged` in its counts when non-zero.
3. Otherwise insert a new row and its `event_refs` mapping.

An incoming structured event is never folded into another row, including a news row about the same incident that arrived first; it gets its own row. This keeps every snapshot-source ref equal to its own row's `events.external_ref`, which `end_missing` relies on. A news report can merge into a structured row of the same group (news road closure into a TfL closure): the row stays owned by the feed, keeps `half_life_min = null` and is ended when the feed drops it. Category groups keep a crime report and a road closure at the same place separate.

## 8. API

| Method | Path | Description |
| :--- | :--- | :--- |
| GET | `/api/cells?res=9&bbox=w,s,e,n&min_score=0.05` | `[{h3, score, live, baseline, top_event_ids}]` |
| GET | `/api/events?bbox=&since=&category=&active=true` | GeoJSON FeatureCollection; properties include current `risk` |
| GET | `/api/events/{id}` | Full event with sources and URLs |
| GET | `/api/agents` | Per source: enabled, interval, last run, last status, counts for the last hour |
| GET | `/api/stream?since=` | SSE (`backend/api_stream.py`): `hello`, `event_upsert`, `event_end`, `cells_changed`, `agent_run`. Details below |
| POST | `/api/route` | Phase 2. `{origin, destination, alpha}` -> `{fast, safe}` each with GeoJSON LineString, length, duration, mean and max risk |

One response shape per endpoint, generated from the Pydantic models. Coordinates in API responses are always GeoJSON order `[lng, lat]`.

### `/api/stream`

Each message is `event: <type>`, `id: <mark>`, `data: <one line of JSON>`. The mark is the high-water timestamp of the rows sent so far (largest `events.updated_at`, `agent_runs.finished_at` or `cell_scores.updated_at`, taken from the stored values, not from the clock).

| `event:` | `data:` |
| :--- | :--- |
| `hello` | `{server_time, mark}`; first message of every connection |
| `event_upsert` | GeoJSON Feature, same shape as an item of `/api/events`, with current `risk` |
| `event_end` | `{id}`; the event has `ended_at` set or its risk is below 0.05 |
| `cells_changed` | `{updated_at}`; `cell_scores` were rewritten (every rescore) |
| `agent_run` | `{id, source_id, finished_at, fetched, inserted, ended, error}` |

- The server reads `db.changes_since(mark)` every 2 s per connection (SQLite has no change notification). The endpoint is a coroutine; only the query runs in the threadpool, so an idle stream holds no thread. A comment line `: ping` is sent after 15 s without output.
- Resume: the `Last-Event-ID` header (sent by EventSource on its own reconnects) takes precedence over `?since=<ISO timestamp>`; without either the stream starts at the current time. The start is limited to the last 15 minutes. Every read starts 10 s before the mark, because writers assign timestamps before their transaction (on Modal before the RPC to the Store), and rows already sent on the connection are skipped. A reconnecting client can therefore receive a message twice; all messages are idempotent.
- The server closes each connection after 60 seconds. The Store is a single container, and on a redeploy the new container starts only after the old one has finished its open requests; a 10-minute stream lifetime caused about 2.5 minutes of downtime per deploy. Every open stream also occupies one of the container's 100 concurrent input slots. EventSource reconnects and resumes from `Last-Event-ID`.
- Risk decay does not write a row, so an event whose risk falls below 0.05 by time alone is not reported; the client reloads `/api/events` every 5 minutes while connected.
- `text/event-stream` is in Starlette's `GZipMiddleware` default exclusion list, so the stream is not compressed or buffered.

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
- Live updates (`web/src/useLiveEvents.ts`): initial load from `/api/events`, then an `EventSource` on `/api/stream` applies `event_upsert` / `event_end` to the list and reloads `/api/agents` on `agent_run`. When the stream has been down for more than 10 s the client polls `/api/events` every 15 s until the stream is open again, and reloads once after a long interruption. Ended events are removed from the list in both modes. The header shows `live` or `polling`; events that arrived after the initial load are marked in the feed for 60 s. Without `VITE_API_BASE` (static sample files) no stream is opened.
- Place search (first sidebar panel): Photon geocoder (`photon.komoot.io`, permits search-as-you-type; Nominatim does not), restricted to the London bbox, with a second request for boroughs and districts because the general search ranks them low. Accepts streets, areas, stations, full postcodes (postcodes.io fallback) and pasted coordinates. Areas and streets fit the map to their bbox; points fly to zoom 15. The last 5 selections are kept in localStorage.
- Layers and filters panel: crime heatmap toggle and opacity; per-source checkboxes with counts (an event merged from several sources stays visible while one of them is enabled); minimum-risk slider; count of hidden events. All settings persist in localStorage under `layerSettings`. Header has Reset view and Hide sidebar.
- Crime heatmap: a `HeatmapLayer` subclass with a ground-distance kernel (300 m, clamped to 30-160 px) and a colour domain in weighted crimes per hectare per month, so colour depends on zoom level only and not on navigation history. Low density is not drawn. Clicking the map (when no event is hit) opens a popup with recorded crimes within 150 m: total, most common categories (from each street point's top 3, not a complete count), the street points with the most crimes, and a percentile against sampled street locations with recorded crime.
- Not built yet: time slider, category filters.

## 10. Routing

Two walking routes are computed server-side per request: `fast` (shortest) and `safe` (risk-weighted). Both use the current `cell_scores`, so routes change as events arrive.

- Graph build (`backend/tools/build_graph.py`, offline): osmnx `network_type="walk"` for inner London (`-0.26, 51.45, 0.02, 51.57`), largest connected component. osmnx and networkx are used only here.
- Graph file: one compact `.npz` (format documented at the top of `backend/routing.py`): node coordinates, directed edge arrays, edge length, `lit` flag from the OSM tag, the res-9 H3 cells each edge passes through (sampled every ~50 m, CSR layout), and edge polylines so routes follow street shapes. Query time needs only numpy and scipy.
- Engine (`backend/routing.py`): endpoints are snapped to the nearest node with a KD-tree (rejected beyond 300 m). Shortest paths use `scipy.sparse.csgraph.dijkstra`.
- Edge risk = `1 - (1 - live) * (1 - 0.4 * baseline)`, the cell score formula, but with a per-street baseline. `live` is the maximum live component of the res-9 cells the edge passes through. `baseline` (`edge_baseline`) is the recorded-crime density around the segment: police.uk street points within 120 m of the segment midpoint, weighted 1 at 0 m down to 0 at 120 m, log-scaled and clipped at the 99.5th percentile of segments. It is computed once per graph and crime month (about 2 s) and cached.
- Why not the cell scores alone: a res-9 cell is about 350 m across, so parallel streets share one value and a detour has to move a whole cell sideways. Measured on three central London routes at alpha 4: cell-based risk gave 0–2% risk reduction; the per-street baseline gives 16–36% for 4–16% extra distance.
- Measured (inner London graph: 154,181 nodes, 200,285 segments, 7.9 MB file, lit tag present on 41% of segments): load 0.14 s, about 50 MB of memory, about 200 ms per request including the cell query.
- Edge cost is a product of positive factors, so it is always above zero and Dijkstra stays valid:

```
cost(e) = length(e) * (1 + alpha * risk(e)) * (1 - beta * lit(e))
alpha in [0, 10] (client slider, default 4), beta = 0.15, lit(e) in {0, 1}
fast = alpha 0, beta 0
```

- `POST /api/route` (`backend/api_route.py`): body `{origin:[lng,lat], destination:[lng,lat], alpha?}`. Returns for `fast` and `safe`: GeoJSON LineString, `length_m`, `duration_min` (1.35 m/s), length-weighted `mean_risk`, `max_risk`; plus `risk_reduction` and `extra_distance_m`. 422 on invalid input or a point too far from a street, 503 when the graph file is missing. Cell scores are read for the bbox of the two points padded by 1.5 km. The graph path comes from `GRAPH_PATH`.
- On Modal the graph is built by the `build_graph` function into the Volume `london-risk-graph` (the public Overpass servers were unreachable or restricted from the development machine), and the `Store` container mounts that Volume.
- Client: `RoutePanel` (pick origin and destination on the map, safety slider, comparison table) and a deck.gl `PathLayer` (fast in grey, safe in green).

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
  routing.py        compact graph format, edge risk and costs, fast/safe shortest paths
  api_route.py      POST /api/route
  api_stream.py     GET /api/stream (SSE)
  features.py       GeoJSON form of an event
  extraction.py     pre-filter, then LLM agent or rule-based extractor
  llm.py            pydantic-ai extraction agent, tools, guardrails
  located.py        Event construction from a geocoded place
  sql/schema.sql
  tests/
web/
  src/ (App, map/, panels/, api.ts, types generated from OpenAPI)
SPEC.md
```

Modal secret `london-risk` (required; attached unconditionally, because a condition that evaluates differently inside the container makes every container fail at start): `LLM_MODEL`, `GOOGLE_API_KEY`, optionally `TFL_APP_KEY`. No database credentials exist.

## 12. Build order

1. Database schema. `scoring.py` with unit tests (decay, merge, cell aggregation).
2. TfL road disruptions poller writing real rows, run locally with `modal run`. This is the first end-to-end path.
3. FastAPI `/api/events` and `/api/cells`; rescore job.
4. Frontend globe with hexagon and event layers reading the live API.
5. Remaining structured pollers (TfL lines/stops, EA floods). Dispatcher cron. `/api/agents` and the fleet panel.
6. police.uk baseline backfill (one-off job, 12 months, tiled over the bbox with `poly=`).
7. Extraction agent path: pre-filter, geocode tool, `fetch_article`, `find_similar_events`, BBC RSS source, merge, the Met feed.
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
