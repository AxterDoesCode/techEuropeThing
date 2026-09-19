// Typed client for the platform API described in docs/API.md.
import type { Feature, Geometry, LineString } from 'geojson'

export const API_BASE: string = (
  (import.meta.env.VITE_API_BASE as string | undefined) ?? 'https://alexchau256--london-risk-store-api.modal.run'
).replace(/\/+$/, '')

export type LngLat = [number, number]

// Optional per-route field; not deployed at the time of writing.
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
  geometry: LineString
  length_m: number
  duration_min: number
  mean_risk: number
  max_risk: number
  steps?: RouteStep[]
  lit_share?: number
  main_road_share?: number
}

export interface RouteResponse {
  fast: RouteLeg
  safe: RouteLeg
  risk_reduction: number
  extra_distance_m: number
  alpha: number
  attribution: string
}

export interface RouteRequest {
  origin: LngLat
  destination: LngLat
  alpha: number
  // ISO 8601 local time with offset. The backend ignores it until night weighting is deployed.
  depart_at?: string
}

export interface AreaRisk {
  mean_score: number
  max_score: number
  mean_live: number
  mean_baseline: number
  london_percentile: number
  cells: number
}

export interface EventProperties {
  id: string
  category: string
  title: string
  summary?: string | null
  source_ids?: string[]
  urls?: string[]
  risk?: number
  distance_m?: number
}

export type EventFeature = Feature<Geometry, EventProperties>

export interface AreaResponse {
  center: LngLat
  radius_m: number
  generated_at: string
  risk: AreaRisk
  crime: { month: string; recorded_crimes: number } | null
  events: EventFeature[]
}

export class ApiError extends Error {
  readonly status: number | null
  constructor(message: string, status: number | null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export const isAbort = (e: unknown): boolean => e instanceof DOMException && e.name === 'AbortError'

// FastAPI returns {"detail": string} for raised errors and {"detail": [{msg}]} for validation errors.
function detailOf(body: unknown): string | null {
  if (typeof body !== 'object' || body === null || !('detail' in body)) return null
  const detail = (body as { detail: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d: unknown) => (typeof d === 'object' && d !== null && 'msg' in d ? String((d as { msg: unknown }).msg) : ''))
      .filter(Boolean)
    return messages.length > 0 ? messages.join('; ') : null
  }
  return null
}

async function request<T>(path: string, init: RequestInit, what: string): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, init)
  } catch (cause) {
    if (isAbort(cause)) throw cause
    throw new ApiError(`${what} failed: the server could not be reached. Check the connection and retry.`, null)
  }
  if (!response.ok) {
    const detail = detailOf(await response.json().catch(() => null))
    throw new ApiError(detail ?? `${what} failed: HTTP ${response.status}`, response.status)
  }
  try {
    return (await response.json()) as T
  } catch {
    throw new ApiError(`${what} failed: unreadable response`, response.status)
  }
}

export function fetchRoute(body: RouteRequest, signal?: AbortSignal): Promise<RouteResponse> {
  return request<RouteResponse>(
    '/api/route',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal },
    'Route request',
  )
}

export function fetchArea(point: LngLat, radiusM: number, signal?: AbortSignal): Promise<AreaResponse> {
  const params = new URLSearchParams({ lat: point[1].toFixed(6), lng: point[0].toFixed(6), radius_m: String(radiusM) })
  return request<AreaResponse>(`/api/area?${params}`, { signal }, 'Area request')
}
