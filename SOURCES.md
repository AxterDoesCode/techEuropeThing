# Data sources

All endpoints are public and need no key. `TFL_APP_KEY` is optional and raises the TfL rate limit.

## Implemented

| Source | Id | Endpoint | Provides |
| :--- | :--- | :--- | :--- |
| TfL road disruptions | `tfl_road` | `https://api.tfl.gov.uk/Road/all/Disruption?stripContent=false` | Roadworks, closures, collisions, planned events; street segments as lines |
| TfL station disruptions | `tfl_transit` | `https://api.tfl.gov.uk/StopPoint/Mode/tube,overground,dlr,elizabeth-line/Disruption` (coordinates: `https://api.tfl.gov.uk/StopPoint/{ids}`) | Station closures, part closures, exit-only, lift and escalator faults |
| Environment Agency floods | `ea_floods` | `https://environment.data.gov.uk/flood-monitoring/id/floods?lat=51.5&long=-0.12&dist=30` (areas: `/id/floodAreas/{id}` and `/id/floodAreas/{id}/polygon`) | Flood warnings with area polygons |
| Met Police news | `met_news` | `https://news.met.police.uk/rss/current_news/66871` | Official incident statements and appeals (RSS, unstructured) |
| BBC London news | `bbc_london` | `https://feeds.bbci.co.uk/news/england/london/rss.xml` | General London news (RSS, unstructured). Polled only when an LLM is configured (`LLM_MODEL`): the rule-based extractor gave mostly false positives on this feed |
| Met Police recorded crime | `police_uk` (one-off backfill) | `https://data.police.uk/api/crimes-street/all-crime?poly=&date=` (months: `https://data.police.uk/api/crimes-street-dates`) | Monthly street-level crime records; baseline layer and heatmap |

## Geocoding (used by unstructured sources)

| Service | Endpoint |
| :--- | :--- |
| postcodes.io | `https://api.postcodes.io/postcodes/{postcode}` |
| Nominatim | `https://nominatim.openstreetmap.org/search` (bounded to the London bbox, 1 request/s) |

Unstructured sources go through `backend/extraction.py`: a headline pre-filter, then the LLM extraction agent (`backend/llm.py`) when `LLM_MODEL` is set, otherwise the rule-based extractor. Reports of the same incident from different sources are merged into one event.

## Planned, not built

| Source | Endpoint | Note |
| :--- | :--- | :--- |

## Removed

| Source | Endpoint | Reason |
| :--- | :--- | :--- |
| LondonAir | `https://api.erg.ic.ac.uk/AirQuality/Hourly/MonitoringIndex/GroupName=London/Json` | Air quality does not change a walking route |
| GDELT doc API | `https://api.gdeltproject.org/api/v2/doc/doc` | Never built. Indexes articles after outlets publish them, so it is slower than polling those outlets' RSS directly; rate limit of 1 request per 5 s |
