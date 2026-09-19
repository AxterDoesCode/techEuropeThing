# Stay London

A hotel finder for London that ranks places to stay by the modelled risk of the 300 m around them, using the London risk platform API (`docs/API.md` in the repository root).

- Search by place name (Photon geocoder), quick picks and a radius of 800 m, 1.5 km or 3 km. The search, sort, filters and the selected hotel are stored in the URL query string.
- Results as a list and a map (MapLibre GL, OpenFreeMap Positron basemap). Sort by lowest modelled risk or by distance; filter by type and "has website".
- Each hotel shows `mean_score` as a labelled bar with the number, and "Modelled risk higher than N% of London".
- Detail panel: recorded crime within 300 m (police.uk records for the month in `crime.month`), most frequent categories and streets, current events with their sources, and the walk from the nearest station (shortest and lower-risk routes) drawn on the map. When the routing service returns 422 or 503 the panel explains why and shows the straight-line distance.
- Compare tray: up to three hotels side by side.

Hotel data comes from OpenStreetMap. There are no prices, photos, reviews or availability. Risk values are modelled values from 0 to 1, not probabilities. `crime.period` and `crime.method` of `/api/area` describe the twelve-month Met Police data behind the baseline of the modelled risk, so the app shows them with the modelled risk and labels the crime counts with `crime.month` only.

## Run

```sh
npm install
npm run dev
```

## Configuration

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `VITE_API_BASE` | `https://alexchau256--london-risk-store-api.modal.run` | Base URL of the platform API |

Set it in `.env.local` or in the environment of the build command.

## Build

```sh
npm run build     # type check and production build, output in dist/
npm run preview   # serve dist/ locally
npm run lint      # oxlint
```

`dist/` is static and can be served from any static host. The app reads its state from the query string only, so no path rewrite rules are needed.

## Source layout

- `src/api.ts`: typed client for `/api/hotels`, `/api/area` and `/api/route`, with an in-memory cache for area and route responses.
- `src/photon.ts`: place search.
- `src/risk.ts`: colour classes (five viridis steps, always shown with the number) and formatting.
- `src/urlState.ts`, `src/hooks/useUrlState.ts`: query-string state.
- `src/hooks/useRequest.ts`: request state with abort of the previous request.
- `src/components/`: search bar, list, cards, map, detail panel, compare tray, "About the data".
