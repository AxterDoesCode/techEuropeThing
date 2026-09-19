# London Live Risk Map

Polling agents write geo-located London events to a SQLite database hosted on Modal, a scoring job converts them to time-decaying risk per H3 cell, and a deck.gl + MapLibre globe displays the result. Design: `SPEC.md`. The original Gemini-generated brief is kept in `HANDOFF_SPEC.md` for reference only.

## Backend

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
.venv/bin/python -m pytest backend/tests
```

Run everything on this machine (no accounts needed). The database is the SQLite file `data/risk.sqlite`:

```bash
.venv/bin/python -m backend.local --police   # first run: also loads the latest month of Met crime data (~30 s)
.venv/bin/python -m backend.local            # poll loop + rescoring + API on http://localhost:8000
```

Run on Modal. The database lives there too: one `Store` container owns the SQLite file and snapshots it to the Modal Volume `london-risk-db` every 30 s. From the repo root, after `modal setup`:

```bash
modal deploy -m backend.app                                # API + dispatcher and rescore crons
modal run -m backend.app::backfill_police                  # latest month of Met Police crime data
modal run -m backend.app::poll_source --source-id tfl_road # one poll by hand
```

Deployed 2026-09-19: API at `https://alexchau256--london-risk-store-api.modal.run`. Storage calls from any app, including the temporary app that `modal run` starts, go to the deployed `Store` by name, so `backfill_police` and manual polls require the app to be deployed first. `modal app stop london-risk` stops all compute; the database snapshot stays in the Volume and is restored on the next deploy.

Walking routes need the street graph. On Modal: `modal run -m backend.app::build_graph` (writes to the Volume `london-risk-graph`), then redeploy so the `Store` loads it. Locally: `python -m backend.tools.build_graph` writes `data/graph/walk.npz` (needs a reachable Overpass server; pass `--overpass-url` for a mirror).

Modal secret `london-risk` (required; the app attaches it unconditionally): `LLM_MODEL=google:gemini-3.8-flash`, `GOOGLE_API_KEY`, optionally `TFL_APP_KEY`.

## Web

```bash
cd web && npm install && npm run dev
```

Without `VITE_API_BASE` the client reads `web/public/sample/*.json`. Refresh those files from the live feeds (TfL road and station disruptions, EA floods, Met Police news, police.uk crime; takes about a minute, no database needed):

```bash
.venv/bin/python -m backend.tools.export_sample web/public/sample
```

With the local backend: `VITE_API_BASE=http://localhost:8000 npm run dev`. With Modal: `VITE_API_BASE=https://<modal-url> npm run dev`.

`maplibre-gl` is pinned to 5.x. deck.gl 9.4 does not work with MapLibre 6.
