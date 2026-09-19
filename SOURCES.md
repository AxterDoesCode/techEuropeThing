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
| Evening Standard London | `standard_london` | `https://www.standard.co.uk/news/london/rss` | London news (RSS, unstructured). Polled only when an LLM is configured |
| MyLondon | `mylondon` | `https://www.mylondon.news/news/?service=rss` | London local news (RSS, unstructured). Polled only when an LLM is configured |
| X (Twitter) | `x_london` | Scraped with `twscrape` through the X website's internal search API, using the session cookies of a throwaway account (`X_AUTH_TOKEN`, `X_CT0` in the secret). No free official read API exists. Outside X's terms; the account can be locked and the library breaks when X changes its API | Public posts matching incident terms in London (two searches, newest 25 posts per poll, `since_id` cursor). Unstructured, `social` confidence (0.35), LLM only, every 5 min. Inactive until the cookies are set. Check cookies with `python -m backend.tools.check_x` |
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
| Reddit (r/london and borough subreddits) | Reddit API (OAuth app needed) | Unstructured, `social` confidence; needs the extraction agent |

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
