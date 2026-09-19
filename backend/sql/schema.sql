create extension if not exists postgis;

create table if not exists sources (
  id text primary key,
  kind text not null check (kind in ('structured', 'unstructured')),
  poll_interval_s int not null,
  enabled boolean not null default true,
  last_polled_at timestamptz,
  last_status text,
  cursor jsonb not null default '{}'
);

create table if not exists raw_items (
  id bigserial primary key,
  source_id text not null references sources(id),
  external_id text not null,
  fetched_at timestamptz not null default now(),
  payload jsonb not null,
  processed boolean not null default false,
  unique (source_id, external_id)
);

create table if not exists events (
  id uuid primary key default gen_random_uuid(),
  -- '<source_id>:<upstream id>' for structured sources; null for extracted events
  external_ref text unique,
  category text not null,
  title text not null,
  summary text,
  geom geometry(Geometry, 4326) not null,
  centroid geometry(Point, 4326) not null,
  radius_m real not null,
  h3_r10 text not null,
  h3_r9 text not null,
  h3_r7 text not null,
  severity real not null check (severity between 0 and 1),
  confidence real not null check (confidence between 0 and 1),
  -- per-source confidence, used to recompute `confidence` on merge
  source_confidence jsonb not null default '{}',
  -- null = no decay while the upstream source still lists the event
  half_life_min real,
  occurred_at timestamptz not null,
  expires_at timestamptz,
  ended_at timestamptz,
  source_ids text[] not null,
  raw_item_ids bigint[] not null default '{}',
  urls text[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists events_geom_idx on events using gist (geom);
create index if not exists events_h3_r9_idx on events (h3_r9);
create index if not exists events_occurred_at_idx on events (occurred_at desc);
create index if not exists events_updated_at_idx on events (updated_at desc);

create table if not exists baseline_cells (
  h3 text primary key,
  res smallint not null,
  crime_rate real not null,
  lit_fraction real
);

-- One document per published month of police.uk data, in the compact client format
create table if not exists crime_points (
  month text primary key,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create table if not exists cell_scores (
  h3 text not null,
  res smallint not null,
  live real not null,
  baseline real not null,
  score real not null,
  top_event_ids uuid[] not null default '{}',
  updated_at timestamptz not null default now(),
  primary key (h3, res)
);

create table if not exists agent_runs (
  id bigserial primary key,
  source_id text not null references sources(id),
  started_at timestamptz not null,
  finished_at timestamptz,
  fetched int not null default 0,
  inserted int not null default 0,
  merged int not null default 0,
  ended int not null default 0,
  llm_calls int not null default 0,
  error text
);
create index if not exists agent_runs_source_started_idx on agent_runs (source_id, started_at desc);

create table if not exists geocode_cache (
  query text primary key,
  lat double precision,
  lng double precision,
  precision_m real,
  provider text,
  created_at timestamptz not null default now()
);

insert into sources (id, kind, poll_interval_s) values
  ('tfl_road', 'structured', 120),
  ('tfl_transit', 'structured', 300),
  ('ea_floods', 'structured', 900),
  ('london_air', 'structured', 900),
  ('met_news', 'unstructured', 600)
on conflict (id) do nothing;
