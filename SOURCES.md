# Data sources

Last reviewed 2026-09-19 against `SOURCES` in `backend/pipeline.py`, the seed rows in `backend/sql/schema.sql` and `backend/alerts.py`.

Scope rule (product decision): the product is about pedestrian safety only. Data that is only about convenience is dropped at ingestion: roadworks, traffic delays, station closures, lift and step-free notices, crowd events. Wide-area official alerts are shown but never scored.

Keys: every data feed below is public and needs no key. `TFL_APP_KEY` is optional and raises the TfL rate limit. News extraction needs an LLM key (`LLM_MODEL`, the project uses Gemini with `GOOGLE_API_KEY`); without it only `met_news` is extracted, with keyword rules.

## Event sources (scored, used for routing)

| Source | Id | Poll | Endpoint | Provides |
| :--- | :--- | :--- | :--- | :--- |
| TfL road incidents | `tfl_road` | 2 min | `https://api.tfl.gov.uk/Road/all/Disruption?stripContent=false` | Incidents only: collisions, emergency-service incidents, hazards, weather flooding, and demonstrations/marches. Roadworks, asset faults, network delays, breakdowns and other planned events are dropped at ingestion; unknown categories are dropped. TfL's severity is ignored (it measures traffic delay); severity is set per incident type. `Road/Meta/Categories` describes a legacy vocabulary that the feed does not use; the real values are listed in the module |
| TfL rail incidents | `tfl_transit` | 5 min | `https://api.tfl.gov.uk/StopPoint/Mode/tube,overground,dlr,elizabeth-line/Disruption` and `https://api.tfl.gov.uk/Line/Mode/tube,overground,dlr,elizabeth-line/Status` (coordinates: `StopPoint/{ids}`, `Line/{id}/StopPoints`) | Real-time items whose text names an emergency cause only: casualty on the track, assault, fire or security alert, police incident, trespasser, evacuation, emergency-services incident, customer or medical incident. Closures, part closures, exit-only, lift/step-free and information notices and operational faults are dropped. Line-status incidents are added only when the named station resolves exactly to a stop on that line. The cause phrases are unverified against live data: no incident was in either feed when this was written |
| Met Police news | `met_news` | 10 min | `https://news.met.police.uk/rss/current_news/66871` | Official incident statements and appeals (RSS, unstructured, `official_statement` confidence 0.9). Most items are court outcomes and are rejected by the pre-filter. Extracted by the LLM when configured, otherwise by keyword rules |
| BBC London news | `bbc_london` | 10 min | `https://feeds.bbci.co.uk/news/england/london/rss.xml` | General London news (RSS, unstructured, `news` confidence 0.7). Polled only when an LLM is configured: the keyword rules gave mostly false positives on this feed |
| Evening Standard London | `standard_london` | 10 min | `https://www.standard.co.uk/news/london/rss` | London news (RSS, unstructured, `news`). LLM only |
| MyLondon | `mylondon` | 10 min | `https://www.mylondon.news/news/?service=rss` | London local news (RSS, unstructured, `news`). LLM only |

Structured feeds (`tfl_*`) are snapshot sources: an event is ongoing while its feed lists it and is marked ended when it disappears. Unstructured sources go through `backend/extraction.py`: headline pre-filter, then the LLM extraction agent (`backend/llm.py`) or the keyword rules, then geocoding. The extractor returns the incident time from the article body; an incident older than its category's hard cap creates no event, and a false alarm ends a matching event. Reports of the same incident from different sources are merged into one event, and each merge refreshes `last_confirmed_at`.

## Crime baseline (static layer: heatmap, cell baseline, route risk)

Loaded by the one-off job `backfill_police` (`backend/sources/mps_lsoa.py`), not polled. Run `build_graph` first: the normalisation uses street lengths from the routing graph.

| Source | Id | Endpoint | Provides |
| :--- | :--- | :--- | :--- |
| MPS recorded crime by LSOA | `mps_lsoa` | London Datastore dataset `exy3m`: resolve the current LSOA 24-month CSV from `https://data.london.gov.uk/api/dataset/exy3m`; boundaries: ONS LSOA 2021 BGC V5 FeatureServer | Crime baseline and heatmap: last 12 months, pedestrian-relevant offences (violence with injury, robbery of personal property, theft from the person, weapons, public fear/alarm/distress, violent disorder, homicide), normalised per km of walkable street from the routing graph (per km2 scaled by the median street density where the graph does not cover an LSOA). City of London uses 12 x the police.uk month because the MPS file is nearly empty there. Weights are placeholders (`REVISIT(crime-weights)`). Run `build_graph` before `backfill_police`. OGL v2 / OGL |
| Met Police street-level crime | `police_uk` | `https://data.police.uk/api/crimes-street/all-crime?poly=&date=` (months: `https://data.police.uk/api/crimes-street-dates`) | Latest month of street-level records. Used only to place each LSOA's value on street points: pedestrian-relevant categories (robbery, violent-crime, theft-from-the-person, possession-of-weapons, public-order); points labelled as recording venues (hospital, police station, prison, supermarket, shopping area, petrol station, educational building, airport) are dropped |

A minor night-time multiplier on this baseline (up to 1.3 at 03:00) is part of the routing branch and not on `main` yet.

## Official alerts (display only)

These are not events: they are stored in the `alerts` table, never scored, never used for routing, and not listed in the agents panel. They are polled every 5 minutes by `poll_alerts`, spawned from the dispatcher. The client shows a small banner for alerts that cover London and are in force or start within 24 hours; amber and red by default (`/api/alerts?min_level=`).

| Source | Id | Endpoint | Notes |
| :--- | :--- | :--- | :--- |
| Met Office weather warnings | `met_office` | `https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/se` | London & South East region; kept when the area list contains "Greater London". Level and hazard come from the title; times are read as UTC (inferred, not documented). Terms: attribute the Met Office and link directly to the warning's page |
| UK Emergency Alerts | `uk_emergency_alerts` | `https://www.gov.uk/alerts/feed.atom` | No levels: every alert is shown as red. In force while there is no stopped stamp; operator tests are dropped. London is matched on the alert's area text (England, UK, London, a borough). OGL |

## Supporting services (not data about risk)

| Service | Endpoint | Used for |
| :--- | :--- | :--- |
| postcodes.io | `https://api.postcodes.io/postcodes/{postcode}` | Geocoding extracted places (backend) and postcode search (client) |
| Nominatim | `https://nominatim.openstreetmap.org/search` | Geocoding extracted places, bounded to the London bbox, 1 request/s through a single `geocode_place` container; results cached in `geocode_cache` |
| Photon | `https://photon.komoot.io/api/` | Client place search (permits search-as-you-type; Nominatim's policy does not) |
| OpenStreetMap via Overpass | `https://overpass-api.de/api/interpreter` and mirrors | Walking graph for routing (`backend/tools/build_graph.py`, osmnx) and hotels/stations for the platform endpoints (`backend/places.py`, table `places`) |
| OpenFreeMap | `https://tiles.openfreemap.org/styles/dark`, `/styles/positron` | Basemap tiles and styles for the client, no key |
| Gemini (through `pydantic-ai`) | model from `LLM_MODEL`, key `GOOGLE_API_KEY` | News extraction agent; chat agent of the platform endpoints |

## Tried and reverted

| Source | What happened |
| :--- | :--- |
| Reddit (six London subreddits, `social` confidence 0.35) | Added in commit `00ff6e0` and reverted in `51561b9` on 2026-09-19. The reason is not recorded in the commit |
| X/Twitter (scraped with twscrape using session cookies, `social` confidence) | Added in `989e51c` and reverted in `17aad84` on 2026-09-19. The reason is not recorded in the commit |

## Removed or declined

| Source | Endpoint | Reason |
| :--- | :--- | :--- |
| TfL roadworks, closures, lift and step-free notices | the two TfL feeds above | Convenience data, not safety. Dropped at ingestion since 2026-09-19 |
| Environment Agency flood warnings (`ea_floods`) | `https://environment.data.gov.uk/flood-monitoring/id/floods` | Removed 2026-09-19 at the owner's request, after having been fixed the same day (PR #2). Its rows are deleted from a deployed database on connect (`RETIRED_SOURCES` in `backend/db.py`). Flooding now reaches the map only through TfL road incidents (Weather / Flooding) and news. Note for anyone re-adding it: the EA gateway blocked an IP (HTTP 403) after about 280 requests in a few minutes |
| GDELT doc API | `https://api.gdeltproject.org/api/v2/doc/doc` | Dropped 2026-09-19: it only discovers articles (no article text, city-level locations), so every hit still needs the extraction agent; direct feeds give the same articles sooner |
| LondonAir | `https://api.erg.ic.ac.uk/AirQuality/Hourly/MonitoringIndex/GroupName=London/Json` | Air quality does not change a walking route |
| Flood Guidance Statement | `https://api.ffc-environment-agency.fgs.metoffice.gov.uk/api/public/v1/statements/latest` | Declined 2026-09-19. County-sized forecast polygons: display only, and the owner did not want it in the banner |
| London Fire Brigade incidents | London Datastore incident records | No live feed exists: open records lag about 7 weeks; the website has HTML write-ups only. Fires arrive through news extraction and TfL road incidents |
| Area reputation ("commonly known dodgy areas"), deprivation indices | none | Dropped as a category 2026-09-19 |

## Adding a source

Every source is a class in `backend/sources/` with an `id`, a `snapshot` flag, and `fetch(cursor) -> (list[RawItem], cursor)`. Each `RawItem` needs a stable `external_id`. Register the instance in `SOURCES` in `backend/pipeline.py` and add a seed row (id, kind, poll interval in seconds) in `backend/sql/schema.sql`. Add a fixture and a test file under `backend/tests/`. Check it without Modal with `python -m backend.local --once`.

Structured feed (location and severity are fields): implement `to_event(raw) -> Event | None` with no network access. `snapshot = True` if every fetch returns all currently active items; items that disappear are then marked ended. Set `empty_is_valid = True` if an empty feed is a normal state. Example: `tfl_road.py`.

Unstructured text (news, social posts): subclass `RssNewsSource` in `rss.py` (three lines for an RSS feed), or for a non-RSS API implement `fetch` plus `article(raw) -> Article` and inherit the rest. Extraction is shared and must not be reimplemented per source: headline pre-filter, then the Gemini extraction agent (`backend/llm.py`, model from `LLM_MODEL`), geocoding, and merging with existing events. Set:

- `requires_llm = True` for general news and social sources. They are skipped while no LLM is configured and never fall back to the keyword rules.
- `source_type`: a key of `SOURCE_TYPE_CONFIDENCE` in `backend/models.py` — `official_statement` 0.9, `news` 0.7, `manual` 0.5, `social` 0.35.

On Modal each new pre-filter candidate is extracted in its own container (`extract_item`, at most `EXTRACT_CONCURRENCY` = 4 at a time); an item is extracted once per version and per extractor, and a failed item is retried on the next poll.
