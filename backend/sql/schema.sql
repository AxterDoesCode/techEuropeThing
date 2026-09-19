-- SQLite schema. Timestamps are ISO 8601 UTC text in one fixed format, so text
-- comparison orders them correctly. Lists and objects are JSON text. Geometry
-- is GeoJSON text; spatial work is done in Python (shapely, H3).

create table if not exists sources (
  id text primary key,
  kind text not null check (kind in ('structured', 'unstructured')),
  poll_interval_s integer not null,
  enabled integer not null default 1,
  last_polled_at text,
  last_status text,
  cursor text not null default '{}'
);

create table if not exists raw_items (
  id integer primary key autoincrement,
  source_id text not null references sources(id),
  external_id text not null,
  fetched_at text not null,
  payload text not null,
  payload_hash text not null,
  unique (source_id, external_id)
);

create table if not exists events (
  id text primary key,
  -- '<source_id>:<upstream id>'; the identity used for upserts
  external_ref text unique,
  category text not null,
  title text not null,
  summary text,
  geometry text not null,
  lng real not null,
  lat real not null,
  radius_m real not null,
  h3_r10 text not null,
  h3_r9 text not null,
  h3_r7 text not null,
  severity real not null check (severity between 0 and 1),
  confidence real not null check (confidence between 0 and 1),
  -- per-source confidence, used to recompute `confidence` on merge
  source_confidence text not null default '{}',
  -- null = no decay while the upstream source still lists the event
  half_life_min real,
  occurred_at text not null,
  expires_at text,
  ended_at text,
  source_ids text not null,
  raw_item_ids text not null default '[]',
  urls text not null default '[]',
  created_at text not null,
  updated_at text not null
);
create index if not exists events_h3_r9_idx on events (h3_r9);
create index if not exists events_updated_at_idx on events (updated_at desc);
create index if not exists events_ended_at_idx on events (ended_at);

-- Every external_ref that created or was merged into an event. This is the
-- lookup used by upserts; events.external_ref holds only the first ref of a row.
create table if not exists event_refs (
  external_ref text primary key,
  event_id text not null references events(id)
);
create index if not exists event_refs_event_id_idx on event_refs (event_id);
-- Backfill for databases created before event_refs existed. No-op otherwise.
insert or ignore into event_refs (external_ref, event_id)
  select external_ref, id from events where external_ref is not null;

create table if not exists baseline_cells (
  h3 text primary key,
  res integer not null,
  crime_rate real not null,
  lit_fraction real
);

-- One document per published month of police.uk data, in the compact client format
create table if not exists crime_points (
  month text primary key,
  payload text not null,
  created_at text not null
);

create table if not exists cell_scores (
  h3 text not null,
  res integer not null,
  live real not null,
  baseline real not null,
  score real not null,
  top_event_ids text not null default '[]',
  updated_at text not null,
  primary key (h3, res)
);

create table if not exists agent_runs (
  id integer primary key autoincrement,
  source_id text not null references sources(id),
  started_at text not null,
  finished_at text,
  fetched integer not null default 0,
  inserted integer not null default 0,
  merged integer not null default 0,
  ended integer not null default 0,
  llm_calls integer not null default 0,
  error text
);
create index if not exists agent_runs_source_started_idx on agent_runs (source_id, started_at desc);

create table if not exists geocode_cache (
  query text primary key,
  -- null lng/lat = the query was tried and did not resolve
  lng real,
  lat real,
  precision_m real,
  label text,
  provider text,
  created_at text not null
);

insert or ignore into sources (id, kind, poll_interval_s) values
  ('tfl_road', 'structured', 120),
  ('tfl_transit', 'structured', 300),
  ('ea_floods', 'structured', 900),
  ('met_news', 'unstructured', 600),
  ('bbc_london', 'unstructured', 600),
  ('standard_london', 'unstructured', 600),
  ('mylondon', 'unstructured', 600),
  ('reddit_london', 'unstructured', 900);
