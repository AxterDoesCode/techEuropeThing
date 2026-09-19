# London Live Risk Map

Polling agents write geo-located London events to Postgres/PostGIS, a scoring job converts them to time-decaying risk per H3 cell, and a deck.gl + MapLibre globe displays the result. Design: `SPEC.md`. The original Gemini-generated brief is kept in `HANDOFF_SPEC.md` for reference only.

## Backend

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m pytest backend/tests
```

Requires a Modal secret named `london-risk` with `DATABASE_URL` (Postgres with PostGIS; optional `TFL_APP_KEY`). Run from the repo root:

```bash
modal run -m backend.app::init_db                          # apply backend/sql/schema.sql
modal run -m backend.app::poll_source --source-id tfl_road # one poll
modal run -m backend.app::rescore                          # recompute cell_scores
modal run -m backend.app::backfill_police                  # latest month of Met Police crime data
modal serve -m backend.app                                 # API with live reload
modal deploy -m backend.app                                # API + dispatcher and rescore crons
```

## Web

```bash
cd web && npm install && npm run dev
```

Without `VITE_API_BASE` the client reads `web/public/sample/*.json`. Refresh those files from the live feeds (TfL road and station disruptions, EA floods, LondonAir, Met Police news, police.uk crime; takes about a minute, no database needed):

```bash
.venv/bin/python -m backend.tools.export_sample web/public/sample
```

With a deployed API: `VITE_API_BASE=https://<modal-url> npm run dev`.

`maplibre-gl` is pinned to 5.x. deck.gl 9.4 does not work with MapLibre 6.
