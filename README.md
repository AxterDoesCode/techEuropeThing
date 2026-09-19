# London Live Risk Map

A safety data platform for Greater London, and four web applications that use it.

The platform polls official feeds and news sources, extracts located incidents from unstructured text with LLM agents, merges reports of the same incident, and combines current events with a police-recorded-crime baseline into a time-decaying risk score per H3 map cell and per street segment. Everything runs on Modal, including the database.

## Live applications

| Application | URL | What it does |
| :--- | :--- | :--- |
| Navigation | https://alexchau256--london-risk-apps-navigate.modal.run | Walking directions that compare the shortest route with a lower-risk route: place search, safety preference, night option, events along the route |
| Hotels | https://alexchau256--london-risk-apps-stay.modal.run | OpenStreetMap hotels ranked by the modelled risk of their surroundings, with area details and the walk from the nearest station. No prices or availability |
| Chat | https://alexchau256--london-risk-apps-ask.modal.run | Ask about an area, a walk or hotels; answers come from the platform's data and the map shows the places, areas and routes each answer refers to |
| Live map | https://alexchau256--london-risk-apps-console.modal.run | deck.gl + MapLibre map of everything the platform holds: crime heatmap, current events, alerts, routes, and the status of each data source |

| Service | URL |
| :--- | :--- |
| Platform API | https://alexchau256--london-risk-store-api.modal.run (`/docs` for the OpenAPI page) |
| Chat API | https://alexchau256--london-risk-chat.modal.run (`POST /api/chat`) |

Risk values are modelled values between 0 and 1, not probabilities. The applications report them together with recorded crime counts and current events, and do not label places as safe or unsafe.

## Repository layout

| Path | Contents |
| :--- | :--- |
| `backend/` | Platform: sources, LLM extraction, merge and scoring, routing, SQLite storage, FastAPI, the Modal app (`backend/app.py`) |
| `web/` | Live map (Vite, React, deck.gl, MapLibre) |
| `apps/navigate`, `apps/stay`, `apps/ask` | The three client applications. Each is its own Vite project and uses only the public API |
| `apps/modal_sites.py` | Static hosting of the four front ends on Modal |
| `docs/API.md` | API contract for client applications |
| `SOURCES.md` | Data sources: in use, tried, removed, and how to add one |
| `SPEC.md` | Design and decisions |
| `HANDOFF_SPEC.md` | The original brief, kept for reference only |

## How it works

1. A dispatcher (Modal cron, every minute) starts a poll for each data source that is due. Structured feeds (TfL incidents) are mapped field by field. News feeds (Met Police newsroom, BBC London, Evening Standard, MyLondon) go through a headline pre-filter and then one LLM extraction agent per article, each in its own container; the agent can read the article, geocode places and look up similar events. Coordinates come only from the geocoder, never from the model.
2. Reports of the same incident from different sources are merged into one event whose confidence rises with each independent source.
3. A scoring job (every minute) combines live events with the crime baseline into a score per H3 cell. Event risk decays over time.
4. The walking graph (OpenStreetMap) gives each street segment a cost from its length, risk, lighting and road class; `/api/route` returns the shortest and the lower-risk route with turn-by-turn steps.
5. One `Store` container owns the SQLite database, serves the API and snapshots the file to a Modal Volume every 30 seconds. The chat agent, the extraction agents, the geocoder and the long jobs run in their own containers.

## Backend

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m pytest backend/tests
```

Run everything on one machine, without Modal. The database is the SQLite file `data/risk.sqlite`:

```bash
.venv/bin/python -m backend.local --police   # first run: also loads the crime data
.venv/bin/python -m backend.local            # poll loop + rescoring + API on http://localhost:8000
```

On Modal (after `modal setup`), from the repo root. Deploys are manual:

```bash
cd web && npm install && npm run build && cd ..   # the Store also serves web/dist at "/"
modal deploy -m backend.app                        # API, chat function, dispatcher and rescore crons
```

Modal secret `london-risk` (required; the app attaches it unconditionally): `LLM_MODEL=google:gemini-3.8-flash`, `GOOGLE_API_KEY`, optionally `TFL_APP_KEY`.

Jobs on the deployed app. Call them by name, so they use the deployed database (`modal run` starts a temporary copy of the app whose crons also fire):

```python
import modal
modal.Function.from_name("london-risk", "refresh_places").remote()    # hotels and stations from OpenStreetMap
modal.Function.from_name("london-risk", "build_graph").spawn()        # walking graph for Greater London, 1-2 hours
modal.Function.from_name("london-risk", "backfill_police").remote()   # crime baseline; run after a graph build
```

Redeploy after `build_graph` or `backfill_police` so the `Store` container reloads the graph and the baseline. A plain `modal deploy` does not cancel calls that are in progress; `modal app stop london-risk` does. The database snapshot stays in the Volume `london-risk-db` and is restored on the next start.

## Front ends

Each front end is a Vite project: `npm install && npm run dev` in `web/`, `apps/navigate`, `apps/stay` or `apps/ask`. They use the deployed API by default; set `VITE_API_BASE` (and `VITE_CHAT_BASE` for `apps/ask`) to point them elsewhere, for example `VITE_API_BASE=http://localhost:8000`. `web/` without `VITE_API_BASE` reads the sample files in `web/public/sample/`.

Publish all four as static sites on Modal (a separate Modal app, `london-risk-apps`, so the sites never share a container with the database):

```bash
for d in web apps/navigate apps/stay apps/ask; do (cd $d && npm install && npm run build); done
modal deploy apps/modal_sites.py
```

`maplibre-gl` is pinned to 5.x in every project: deck.gl 9.4 does not work with MapLibre 6.

## Data and attribution

Police-recorded crime: data.police.uk and Metropolitan Police Service open data (Open Government Licence). Transport incidents: Transport for London Unified API. Map, walking network, hotels and stations: © OpenStreetMap contributors (ODbL). News items link to their publishers. Details and the list of sources that were tried and removed are in `SOURCES.md`.
