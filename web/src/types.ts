import type { Feature, FeatureCollection, Geometry } from 'geojson'

export type Category =
  | 'violent_crime'
  | 'property_crime'
  | 'disorder'
  | 'fire'
  | 'road_closure'
  | 'transit_disruption'
  | 'flood'
  | 'weather'
  | 'other'

export interface EventProps {
  id: string
  category: Category
  title: string
  summary: string | null
  lng: number
  lat: number
  radius_m: number
  severity: number
  confidence: number
  half_life_min: number | null
  occurred_at: string
  expires_at: string | null
  ended_at: string | null
  source_ids: string[]
  urls: string[]
  risk: number
}

export type EventFeature = Feature<Geometry, EventProps>
export type EventCollection = FeatureCollection<Geometry, EventProps>

export interface LngLat {
  lng: number
  lat: number
}

// [west, south, east, north]
export type BBox = [number, number, number, number]

// Where the map is asked to move: a position with an optional zoom, or an area
export type FlyTarget = (LngLat & { zoom?: number }) | { bbox: BBox }

export interface Cell {
  h3: string
  res: number
  live: number
  baseline: number
  score: number
  top_event_ids: string[]
}

export interface AgentStatus {
  id: string
  kind: string
  enabled: boolean
  poll_interval_s: number
  last_polled_at: string | null
  last_status: string | null
  runs_last_hour: number
  fetched_last_hour: number
  inserted_last_hour: number
  errors_last_hour: number
}

// [lng, lat, count, weighted, street, top categories]
export type CrimeRow = [number, number, number, number, string, Record<string, number>]

export interface CrimePoints {
  month: string
  columns: string[]
  rows: CrimeRow[]
}

export interface RouteStep {
  instruction: string
  street: string | null
  distance_m: number
  duration_s: number
  // the majority of the step's length is lit
  lit: boolean
  // length-weighted mean risk of the step
  risk: number
  start: [number, number]
}

export interface RouteLeg {
  geometry: { type: 'LineString'; coordinates: [number, number][] }
  length_m: number
  duration_min: number
  mean_risk: number
  // The safe route minimises a cost, not the maximum: its max_risk can be above the fast route's
  max_risk: number
  // The fields below are absent in responses of a backend older than the road-class graph.
  // Placeholder whole-route score in [0, 1): 1000 m at risk 0.5 gives 0.5
  path_risk?: number
  // share of the length that is lit / on trunk, primary, secondary or tertiary road centrelines
  lit_share?: number
  main_road_share?: number
  park_m?: number
  underpass_m?: number
  steps?: RouteStep[]
}

export interface RouteResult {
  fast: RouteLeg
  safe: RouteLeg
  alpha: number
  beta: number
  gamma?: number
  // factor applied to the crime baseline for the departure time, 1 by day up to 1.3 at 03:00
  night_multiplier?: number
  risk_reduction: number
  extra_distance_m: number
  attribution: string
}

export type RouteEndpoint = 'origin' | 'destination'
