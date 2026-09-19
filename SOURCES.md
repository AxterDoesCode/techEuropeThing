# Data sources

All endpoints are public and need no key. `TFL_APP_KEY` is optional and raises the TfL rate limit.

## Implemented

| Source | Id | Endpoint | Provides |
| :--- | :--- | :--- | :--- |
| TfL road incidents | `tfl_road` | `https://api.tfl.gov.uk/Road/all/Disruption?stripContent=false` | Incidents only: collisions, emergency-service incidents, hazards, weather flooding, and demonstrations/marches. Roadworks, asset faults, network delays, breakdowns and other planned events are dropped at ingestion; unknown categories are dropped. TfL's severity is ignored (it measures traffic delay); severity is set per incident type. `Road/Meta/Categories` describes a legacy vocabulary that the feed does not use; the real values are listed in the module |
| TfL rail incidents | `tfl_transit` | `https://api.tfl.gov.uk/StopPoint/Mode/tube,overground,dlr,elizabeth-line/Disruption` and `https://api.tfl.gov.uk/Line/Mode/tube,overground,dlr,elizabeth-line/Status` (coordinates: `StopPoint/{ids}`, `Line/{id}/StopPoints`) | Real-time items whose text names an emergency cause only: casualty on the track, assault, fire or security alert, police incident, trespasser, evacuation, emergency-services incident, customer or medical incident. Closures, part closures, exit-only, lift/step-free and information notices and operational faults are dropped. Line-status incidents are added only when the named station resolves exactly to a stop on that line. The cause phrases are unverified against live data: no incident was in either feed when this was written |
| Environment Agency floods | `ea_floods` | `https://environment.data.gov.uk/flood-monitoring/id/floods?lat=51.48935&long=-0.08820&dist=45` (centre and radius derived from the London bbox; areas: `/id/floodAreas/{id}` and `/id/floodAreas/{id}/polygon`) | Flood alerts, warnings and severe warnings with area polygons. Kept when the area polygon intersects the London bbox. Area lookups: 4 concurrent, 60 new areas per poll at most. The EA gateway blocks an IP (HTTP 403 on all endpoints) after a few hundred requests in a few minutes; an error status fails the poll and ends no events |
| Met Police news | `met_news` | `https://news.met.police.uk/rss/current_news/66871` | Official incident statements and appeals (RSS, unstructured) |
| BBC London news | `bbc_london` | `https://feeds.bbci.co.uk/news/england/london/rss.xml` | General London news (RSS, unstructured). Polled only when an LLM is configured (`LLM_MODEL`): the rule-based extractor gave mostly false positives on this feed |
| Evening Standard London | `standard_london` | `https://www.standard.co.uk/news/london/rss` | London news (RSS, unstructured). Polled only when an LLM is configured |
| MyLondon | `mylondon` | `https://www.mylondon.news/news/?service=rss` | London local news (RSS, unstructured). Polled only when an LLM is configured |
| MPS recorded crime by LSOA | `mps_lsoa` (one-off backfill `backfill_police`) | London Datastore dataset `exy3m`: resolve the current LSOA 24-month CSV from `https://data.london.gov.uk/api/dataset/exy3m`; boundaries: ONS LSOA 2021 BGC V5 FeatureServer | Crime baseline and heatmap: last 12 months, pedestrian-relevant offences (violence with injury, robbery of personal property, theft from the person, weapons, public fear/alarm/distress, violent disorder, homicide), normalised per km of walkable street from the routing graph (per km2 scaled by the median street density where the graph does not cover an LSOA). City of London uses 12 x the police.uk month because the MPS file is nearly empty there. Weights are placeholders (`REVISIT(crime-weights)`). Run `build_graph` before `backfill_police`. OGL v2 / OGL |
| Met Police street-level crime | `police_uk` (part of the baseline backfill) | `https://data.police.uk/api/crimes-street/all-crime?poly=&date=` (months: `https://data.police.uk/api/crimes-street-dates`) | Latest month of street-level records. Used only to place each LSOA's value on street points: pedestrian-relevant categories (robbery, violent-crime, theft-from-the-person, possession-of-weapons, public-order); points labelled as recording venues (hospital, police station, prison, supermarket, shopping area, petrol station, educational building, airport) are dropped |

## Official alerts (display only)

These are not events: they are stored in the `alerts` table, never scored, never used for routing, and not listed in the agents panel. They are polled every 5 minutes by `poll_alerts`, spawned from the dispatcher. The client shows a small banner for alerts that cover London and are in force or start within 24 hours; amber and red by default (`/api/alerts?min_level=`).

| Source | Id | Endpoint | Notes |
| :--- | :--- | :--- | :--- |
| Met Office weather warnings | `met_office` | `https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/se` | London & South East region; kept when the area list contains "Greater London". Level and hazard come from the title; times are read as UTC (inferred, not documented). Terms: attribute the Met Office and link directly to the warning's page |
| UK Emergency Alerts | `uk_emergency_alerts` | `https://www.gov.uk/alerts/feed.atom` | No levels: every alert is shown as red. In force while there is no stopped stamp; operator tests are dropped. London is matched on the alert's area text (England, UK, London, a borough). OGL |

## Geocoding (used by unstructured sources)

| Service | Endpoint |
| :--- | :--- |
| postcodes.io | `https://api.postcodes.io/postcodes/{postcode}` |
| Nominatim | `https://nominatim.openstreetmap.org/search` (bounded to the London bbox, 1 request/s) |

Unstructured sources go through `backend/extraction.py`: a headline pre-filter, then the LLM extraction agent (`backend/llm.py`) when `LLM_MODEL` is set, otherwise the rule-based extractor. Reports of the same incident from different sources are merged into one event.

## Planned, not built

| Source | Endpoint | Note |
| :--- | :--- | :--- |
| Reddit (r/london and borough subreddits) | Reddit API (OAuth app needed) | Unstructured, `social` confidence; needs the extraction agent |
| Twitter/X | Paid API | Only if access is available; unstructured, `social` confidence |

## Removed

| Source | Endpoint | Reason |
| :--- | :--- | :--- |
| GDELT doc API | `https://api.gdeltproject.org/api/v2/doc/doc` | Dropped 2026-09-19: it only discovers articles (no article text, city-level locations), so every hit still needs the extraction agent; direct feeds give the same articles sooner |
| LondonAir | `https://api.erg.ic.ac.uk/AirQuality/Hourly/MonitoringIndex/GroupName=London/Json` | Air quality does not change a walking route |

## Adding a source

Every source is a class in `backend/sources/` with an `id`, a `snapshot` flag, and `fetch(cursor) -> (list[RawItem], cursor)`. Each `RawItem` needs a stable `external_id`. Register the instance in `SOURCES` in `backend/pipeline.py` and add a seed row (id, kind, poll interval in seconds) in `backend/sql/schema.sql`. Add a fixture and a test file under `backend/tests/`. Check it without Modal with `python -m backend.local --once`.

Structured feed (location and severity are fields): implement `to_event(raw) -> Event | None` with no network access. `snapshot = True` if every fetch returns all currently active items; items that disappear are then marked ended. Set `empty_is_valid = True` if an empty feed is a normal state. Example: `tfl_road.py`.

Unstructured text (news, social posts): subclass `RssNewsSource` in `rss.py` (three lines for an RSS feed), or for a non-RSS API implement `fetch` plus `article(raw) -> Article` and inherit the rest. Extraction is shared and must not be reimplemented per source: headline pre-filter, then the Gemini extraction agent (`backend/llm.py`, model from `LLM_MODEL`), geocoding, and merging with existing events. Set:

- `requires_llm = True` for general news and social sources. They are skipped while no LLM is configured and never fall back to the keyword rules.
- `source_type`: a key of `SOURCE_TYPE_CONFIDENCE` in `backend/models.py` — `official_statement` 0.9, `news` 0.7, `manual` 0.5, `social` 0.35.

On Modal each new pre-filter candidate is extracted in its own container (`extract_item`, at most `EXTRACT_CONCURRENCY` = 4 at a time); an item is extracted once per version and per extractor, and a failed item is retried on the next poll.
