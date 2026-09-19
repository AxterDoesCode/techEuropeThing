import type { Feature, FeatureCollection, Geometry } from 'geojson'

export type Category =
  | 'violent_crime'
  | 'property_crime'
  | 'disorder'
  | 'fire'
  | 'road_closure'
  | 'transit_disruption'
  | 'flood'
  | 'air_quality'
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
