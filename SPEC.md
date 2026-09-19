# London Live Risk Map — Build Specification

Replaces `HANDOFF_SPEC.md`. Scope: Greater London only. Hackathon proof of concept that runs on real data end to end; no hardcoded incidents, scores, or routes.

## 1. Product

A fleet of polling agents continuously writes geo-located events (crime, disorder, fires, transport disruption, flooding, air quality, road closures) into one central database. Every event is keyed by a geometry and an H3 cell. A scoring job turns events into a per-cell risk value that decays over time. A web client renders a 3D globe, focused on London, with risk cells highlighted and individual events inspectable. Phase 2 adds walking routes that weight street segments by the same cell risk.

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
| Database | Hosted Postgres with PostGIS (Supabase or Neon). Must be reachable from Modal, so not SQLite and not `modal.Dict` |
| Spatial key | H3. Events stored at resolution 10, scores aggregated at resolutions 9 (street level, ~175 m edge) and 7 (city level, ~1.2 km edge). H3 indexes computed in Python with `h3`, stored as `text`; no dependency on the `h3-pg` extension |
| LLM | Provider-agnostic. Use `pydantic-ai`; model selected by env var, e.g. `LLM_MODEL=anthropic:claude-haiku-4-5`, `google-gla:gemini-2.5-flash`, `openai:gpt-...`. Output type is the Pydantic event model |
| Globe | deck.gl 9.4 on MapLibre GL JS **5.x** with `projection: globe`, `MapboxOverlay({interleaved: true})`. Verified 2026-09-19: hexagons, pitch and extrusion render correctly on the globe. MapLibre 6 must not be used: it removed `map.transform`, which deck.gl 9.4 reads, and every frame throws. Keyless basemap: OpenFreeMap `dark` style |
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
| LondonAir (Imperial ERG) | `api.erg.ic.ac.uk/AirQuality/Hourly/MonitoringIndex/GroupName=London/Json` | 200, no key | Hourly | No | Air quality index per monitoring site, with lat/lon |
| Open-Meteo | `api.open-meteo.com/v1/forecast?...&current=` | 200, no key | 15 min | No | Wind gusts, heavy rain as a city-wide modifier |
| BBC London RSS | `feeds.bbci.co.uk/news/england/london/rss.xml` | 200 | Minutes to hours | Yes | Unstructured incident reports; requires extraction + geocoding |
| GDELT doc API | `api.gdeltproject.org/api/v2/doc/doc?...` | 429 when called back to back; limit is 1 request per 5 s | 15 min | Yes | Wider news coverage. Poll at most once per minute |
| Met Police news | `news.met.police.uk/rss/latest_news` returned 404 | Find the current feed URL on the Mynewsdesk newsroom page | Hours | Yes | Official incident statements |

Not yet checked, worth adding if time allows: London Fire Brigade incident data (London Datastore), TfL JamCam locations (CCTV coverage proxy), OSM `lit=yes/no` tags (street lighting, from the same OSM extract used for routing), additional local RSS (Evening Standard, MyLondon).

Dropped from the Gemini spec: X/Twitter geo posts (no usable access), satellite night-light data (VIIRS resolution is ~500 m, useless per street), "Police CAD" (no public dispatch feed exists for London).

TfL allows anonymous calls at a low rate; register a free `app_key` to avoid throttling.

## 4. Architecture

```
Modal cron: dispatcher (every 1 min)
   reads `sources` table -> for each source that is due: spawn poll_source(source_id)

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

1. **Structured pollers** — TfL, EA floods, LondonAir, Open-Meteo, police.uk. Deterministic field mapping. No LLM.
2. **Extraction agents** — RSS, GDELT, manual inject. A tool-using LLM agent (`pydantic-ai`) with output type `list[ExtractedEvent]`; one article can describe zero, one or several incidents.

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

```sql
create extension if not exists postgis;

create table sources (
  id text primary key,                 -- 'tfl_road', 'bbc_london', ...
  kind text not null,                  -- 'structured' | 'unstructured'
  poll_interval_s int not null,
  enabled boolean not null default true,
  last_polled_at timestamptz,
  last_status text,                    -- 'ok' | 'error: ...'
  cursor jsonb                         -- etag, last seen id, etc.
);

create table raw_items (
  id bigserial primary key,
  source_id text references sources(id),
  external_id text not null,           -- upstream id, or sha256 of content
  fetched_at timestamptz not null default now(),
  payload jsonb not null,
  processed boolean not null default false,
  unique (source_id, external_id)
);

create table events (
  id uuid primary key default gen_random_uuid(),
  category text not null,
  title text not null,
  summary text,
  geom geometry(Geometry, 4326) not null,   -- point or polygon
  centroid geometry(Point, 4326) not null,
  radius_m real not null,
  h3_r10 text not null,
  h3_r9 text not null,
  h3_r7 text not null,
  severity real not null check (severity between 0 and 1),
  confidence real not null check (confidence between 0 and 1),
  half_life_min real not null,
  occurred_at timestamptz not null,
  expires_at timestamptz,                   -- set when upstream gives an end time
  ended_at timestamptz,                     -- when upstream stopped listing it
  external_ref text unique,                 -- '<source_id>:<upstream id>' for structured sources
  source_confidence jsonb not null,         -- per-source confidence, recombined on merge
  source_ids text[] not null,
  raw_item_ids bigint[] not null,
  urls text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on events using gist (geom);
create index on events (h3_r9);
create index on events (occurred_at desc);

create table baseline_cells (               -- from police.uk + OSM lighting
  h3 text primary key, res smallint not null,
  crime_rate real not null,                 -- normalised 0..1 across London
  lit_fraction real                         -- phase 2
);

create table cell_scores (
  h3 text not null, res smallint not null,
  live real not null, baseline real not null, score real not null,
  top_event_ids uuid[] not null,
  updated_at timestamptz not null,
  primary key (h3, res)
);

create table agent_runs (
  id bigserial primary key,
  source_id text references sources(id),
  started_at timestamptz not null, finished_at timestamptz,
  fetched int, inserted int, merged int, llm_calls int, error text
);

create table geocode_cache (query text primary key, lat double precision, lng double precision, precision_m real, provider text);
```

## 6. Pydantic models (`backend/models.py`)

- `Category` enum: `violent_crime`, `property_crime`, `disorder`, `fire`, `road_closure`, `transit_disruption`, `flood`, `air_quality`, `weather`, `other`.
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
| air_quality | 60 | 1000 | 0.9 |

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

- Initial view: whole globe, then an animated camera move to London (pitch ~50°).
- Layers (deck.gl):
  - `H3HexagonLayer` — res 7 when zoom < 11, res 9 otherwise. Colour and extrusion height from `score`. Transition on update.
  - `ScatterplotLayer` / `IconLayer` — active events by category; radius pulse for events newer than 10 minutes.
  - `GeoJsonLayer` — flood polygons, road closure geometry.
  - `PathLayer` — routes (phase 2).
  - MapLibre `fill-extrusion` buildings from the vector basemap for the 3D city appearance.
- Panels: event feed (newest first, click to fly to), event detail (sources, links, risk-over-time sparkline from the decay formula), agent fleet panel (from `/api/agents`), category and time-window filters, a time slider that replays the last 24 h by recomputing `r_i(t)` client-side.
- Removed from the Gemini spec: Python source viewer modal, Google Maps key modal, Express server, fixed "96% / 38%" labels.

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
  db.py             psycopg pool, queries
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

Secrets (Modal secret `london-risk`): `DATABASE_URL`, `LLM_MODEL`, the matching provider key, optional `TFL_APP_KEY`.

## 12. Build order

1. Postgres + schema. `scoring.py` with unit tests (decay, merge, cell aggregation).
2. TfL road disruptions poller writing real rows, run locally with `modal run`. This is the first end-to-end path.
3. FastAPI `/api/events` and `/api/cells`; rescore job.
4. Frontend globe with hexagon and event layers reading the live API.
5. Remaining structured pollers (TfL lines/stops, EA floods, LondonAir). Dispatcher cron. `/api/agents` and the fleet panel.
6. police.uk baseline backfill (one-off job, 12 months, tiled over the bbox with `poly=`).
7. Extraction agent path: pre-filter, geocode tool, `fetch_article`, `find_similar_events`, BBC RSS source, merge. Then GDELT and the Met feed. `/api/inject`.
8. SSE, time slider, visual polish.
9. Phase 2 routing.
10. Stretch: corroboration agent, LFB data, lighting.

Working demo exists after step 4; every later step adds data or features without restructuring.

## 13. Known risks

- deck.gl + MapLibre globe projection compatibility: check first, fallback listed in section 2.
- Geocoding accuracy for news text is the main source of wrong events. Mitigation: low confidence and large radius for imprecise matches, and discard when no place resolves.
- Live violent-crime signal for London is thin because no real-time police feed exists; most real-time volume will be transport, roads, floods, air quality. News-derived events fill part of the gap. The baseline layer carries the crime picture.
- Modal SSE connection duration and cron count limits: verify on the actual plan.
- Nominatim usage policy (1 req/s, identify with a User-Agent). Cache all results.
