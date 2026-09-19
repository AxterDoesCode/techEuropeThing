# Navigate: lower-risk walking routes in London

A single-page walking directions app for London. It compares the shortest walking route with a route
that the platform's router weights by modelled risk, and shows current events near the chosen route.
It is a client of the platform API described in `docs/API.md`; it shares no code with `web/`.

Risk values are modelled values in [0, 1] (current events combined with police-recorded crime for one
recent month). They are not probabilities, and the crime data has no time of day.

## Run

```
npm install
npm run dev
```

## Configuration

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `VITE_API_BASE` | `https://alexchau256--london-risk-store-api.modal.run` | Base URL of the platform API |

Set it in `.env.local` or in the environment of `npm run dev` / `npm run build`.

## Build

```
npm run build
```

Type-checks with `tsc -b`, then writes a static site to `dist/`. Asset paths are relative, so `dist/`
can be served from any URL path by any static file host. `npm run preview` serves `dist/` locally.

## Features

- From / To place search with autocomplete (Photon, restricted to Greater London), "Use my location",
  map click to set the active field, swap, draggable markers.
- One `POST /api/route` per change of inputs; both routes are drawn and compared in two cards.
- "Safety preference" slider (`alpha` 0-10) and "Walking at night" toggle (sends `depart_at` for
  23:00 local time today and switches to the dark basemap).
- Turn-by-turn list when the response has `steps`; a trip summary when it does not.
- "Along this route": `GET /api/area` (250 m) at 3-5 points of the selected route, events de-duplicated by id.
- Query string state: `from`, `to` (`lng,lat`), `from_name`, `to_name`, `alpha`, `night`.

## Source layout

- `src/api.ts` typed API client; `src/geocode.ts` Photon search and reverse geocoding.
- `src/useRoute.ts`, `src/useAlongRoute.ts` request state with abort of superseded requests.
- `src/components/` map, search card, route cards, preferences, step list, events, data notes.

Routing covers inner London at present. Outside that area the API answers 422 and the app shows the
message from the API.
