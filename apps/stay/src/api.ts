// Typed client for the London risk platform API (see docs/API.md in the repository root).

export const API_BASE: string = (
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  'https://alexchau256--london-risk-store-api.modal.run'
).replace(/\/+$/, '')

/** [lng, lat] */
export type LngLat = [number, number]

export interface RiskBlock {
  mean_score: number
  max_score: number
  mean_live: number
  mean_baseline: number
  london_percentile: number
  cells: number
}

export type HotelSubtype = 'hotel' | 'hostel' | 'guest_house' | 'apartment'

export interface HotelDetails {
  subtype?: string
  stars?: string
  website?: string
  phone?: string
  address?: string
}

export interface Station {
  name: string
  lng: number
  lat: number
  distance_m: number
}

export interface Hotel {
  id: string
  kind: string
  name: string
  lng: number
  lat: number
  details: HotelDetails
  distance_m: number
  risk: RiskBlock | null
  nearest_station: Station | null
}

export interface HotelsResponse {
  center: LngLat
  radius_m: number
  total: number
  hotels: Hotel[]
  attribution?: string
}

export type HotelSort = 'safety' | 'distance'

export interface EventProperties {
  id: string
  category: string
  title: string
  summary?: string | null
  severity?: number
  occurred_at?: string | null
  source_ids?: string[]
  urls?: string[]
  risk?: number
  distance_m?: number
}

export interface EventFeature {
  type: 'Feature'
  id?: string
  geometry: { type: string } | null
  properties: EventProperties
}

export interface CrimeStreet {
  street: string
  recorded_crimes: number
}

export interface CrimeBlock {
  /** Month ("YYYY-MM") of the police.uk records behind `recorded_crimes`, `top_categories` and `top_streets`. */
  month: string
  /**
   * Period ("YYYY-MM..YYYY-MM") of the Met Police data behind the baseline of the modelled risk.
   * It does not describe the counts in this block. Absent on older deployments.
   */
  period?: string | null
  /** Sentence on how the baseline of the modelled risk is derived. */
  method?: string | null
  recorded_crimes: number
  weighted: number
  top_categories: Record<string, number>
  top_streets: CrimeStreet[]
}

export interface AreaResponse {
  center: LngLat
  radius_m: number
  generated_at: string
  risk: RiskBlock | null
  crime: CrimeBlock | null
  events: EventFeature[]
}

export interface RouteStep {
  instruction: string
  street: string | null
  distance_m: number
  duration_s: number
  lit: boolean
  risk: number
  start: LngLat
}

export interface RouteLeg {
  geometry: { type: 'LineString'; coordinates: LngLat[] }
  length_m: number
  duration_min: number
  mean_risk: number
  max_risk: number
  // The fields below were added to the service later; clients must work without them.
  steps?: RouteStep[]
  /** Share of the route length on lit streets, 0 to 1. */
  lit_share?: number
  /** Share of the route length on main roads, 0 to 1. */
  main_road_share?: number
  path_risk?: number
}

export interface RouteResponse {
  fast: RouteLeg
  safe: RouteLeg
  risk_reduction: number
  extra_distance_m: number
  alpha: number
  night_multiplier?: number
  attribution?: string
}

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** FastAPI returns {"detail": string} or {"detail": [{msg}]} on errors. */
function detailOf(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('detail' in body)) return null
  const detail = (body as { detail: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d: unknown) =>
        typeof d === 'object' && d !== null && 'msg' in d ? String((d as { msg: unknown }).msg) : '',
      )
      .filter(Boolean)
    return messages.length > 0 ? messages.join('; ') : null
  }
  return null
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, init)
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'The data service could not be reached. Check your connection and try again.')
  }
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(response.status, detailOf(body) ?? `Request failed with status ${response.status}`)
  }
  return (await response.json()) as T
}

// Successful area and route responses are kept in memory so the detail panel and the
// compare tray do not request the same circle or walk twice.
const areaCache = new Map<string, AreaResponse>()
const routeCache = new Map<string, RouteResponse>()

export function fetchHotels(
  params: { lat: number; lng: number; radius_m: number; sort: HotelSort },
  signal: AbortSignal,
): Promise<HotelsResponse> {
  const query = new URLSearchParams({
    lat: String(params.lat),
    lng: String(params.lng),
    radius_m: String(params.radius_m),
    sort: params.sort,
  })
  return request<HotelsResponse>(`/api/hotels?${query}`, { signal })
}

export async function fetchArea(
  params: { lat: number; lng: number; radius_m: number },
  signal: AbortSignal,
): Promise<AreaResponse> {
  const key = `${params.lat.toFixed(6)},${params.lng.toFixed(6)},${params.radius_m}`
  const cached = areaCache.get(key)
  if (cached) return cached
  const query = new URLSearchParams({
    lat: String(params.lat),
    lng: String(params.lng),
    radius_m: String(params.radius_m),
  })
  const data = await request<AreaResponse>(`/api/area?${query}`, { signal })
  areaCache.set(key, data)
  return data
}

export async function fetchRoute(
  origin: LngLat,
  destination: LngLat,
  signal: AbortSignal,
): Promise<RouteResponse> {
  const key = JSON.stringify([origin, destination])
  const cached = routeCache.get(key)
  if (cached) return cached
  const data = await request<RouteResponse>('/api/route', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ origin, destination }),
    signal,
  })
  routeCache.set(key, data)
  return data
}
