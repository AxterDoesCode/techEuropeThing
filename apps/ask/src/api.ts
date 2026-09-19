// Typed client for the two platform services described in docs/API.md.

export const CHAT_BASE: string =
  import.meta.env.VITE_CHAT_BASE ?? 'https://alexchau256--london-risk-chat.modal.run'
export const API_BASE: string =
  import.meta.env.VITE_API_BASE ?? 'https://alexchau256--london-risk-store-api.modal.run'

export const MAX_MESSAGES = 30
export const MAX_MESSAGE_CHARS = 2000
export const RATE_LIMIT = 20
export const RATE_WINDOW_MS = 600_000

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface Source {
  title: string
  url: string
}

export interface Place {
  query: string
  found: boolean
  label: string
  lat: number
  lng: number
  precision_m: number | null
}

export interface ChatResponse {
  answer: string
  sources: Source[]
  places: Place[]
  tool_calls: string[]
}

export type LngLat = [number, number]

export interface RouteLeg {
  geometry: { type: 'LineString'; coordinates: LngLat[] }
  length_m: number
  duration_min: number
  mean_risk: number
  max_risk: number
  // Optional fields added by later versions of the routing service.
  path_risk?: number
  lit_share?: number
  main_road_share?: number
  steps?: RouteStep[]
}

export interface RouteStep {
  instruction: string
  street: string | null
  distance_m: number
}

export interface RouteResponse {
  fast: RouteLeg
  safe: RouteLeg
  risk_reduction?: number
  extra_distance_m?: number
  night_multiplier?: number
}

export interface Hotel {
  id: string
  name: string
  lng: number
  lat: number
  distance_m: number
  risk: { mean_score: number; london_percentile: number } | null
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  // status 0 means the request did not produce an HTTP response.
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

// FastAPI returns `detail` as a string for HTTPException and as a list of
// objects for validation errors.
function detailText(body: unknown, fallback: string): string {
  if (isRecord(body)) {
    const detail = body.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const parts = detail
        .map((d) => (isRecord(d) && typeof d.msg === 'string' ? d.msg : null))
        .filter((m): m is string => m !== null)
      if (parts.length > 0) return parts.join('; ')
    }
  }
  return fallback
}

async function requestJson(url: string, init: RequestInit): Promise<unknown> {
  let resp: Response
  try {
    resp = await fetch(url, init)
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, 'The service could not be reached. Check the network connection.')
  }
  let body: unknown = null
  try {
    body = await resp.json()
  } catch {
    body = null
  }
  if (!resp.ok) throw new ApiError(resp.status, detailText(body, `HTTP ${resp.status}`))
  return body
}

// Reads the documented fields only; unknown top-level fields (for example a
// future `ui` object) are ignored.
export function normaliseChatResponse(body: unknown): ChatResponse {
  if (!isRecord(body) || typeof body.answer !== 'string') {
    throw new ApiError(502, 'The service returned a response without an answer.')
  }
  const sources: Source[] = []
  if (Array.isArray(body.sources)) {
    for (const s of body.sources) {
      if (isRecord(s) && typeof s.url === 'string') {
        sources.push({ title: typeof s.title === 'string' && s.title ? s.title : s.url, url: s.url })
      }
    }
  }
  const places: Place[] = []
  if (Array.isArray(body.places)) {
    for (const p of body.places) {
      if (isRecord(p) && typeof p.query === 'string' && isFiniteNumber(p.lat) && isFiniteNumber(p.lng)) {
        places.push({
          query: p.query,
          found: p.found !== false,
          label: typeof p.label === 'string' ? p.label : p.query,
          lat: p.lat,
          lng: p.lng,
          precision_m: isFiniteNumber(p.precision_m) ? p.precision_m : null,
        })
      }
    }
  }
  const tool_calls = Array.isArray(body.tool_calls)
    ? body.tool_calls.filter((t): t is string => typeof t === 'string')
    : []
  return { answer: body.answer, sources, places, tool_calls }
}

// The API accepts at most 30 messages of at most 2000 characters. The most
// recent 30 are kept, leading assistant messages are dropped so the list
// starts with a user message, and long contents are cut to the limit.
export function trimHistory(messages: ChatMessage[]): ChatMessage[] {
  let recent = messages.slice(-MAX_MESSAGES)
  const firstUser = recent.findIndex((m) => m.role === 'user')
  recent = firstUser === -1 ? [] : recent.slice(firstUser)
  return recent.map((m) => ({ role: m.role, content: m.content.slice(0, MAX_MESSAGE_CHARS) }))
}

export async function postChat(messages: ChatMessage[], signal?: AbortSignal): Promise<ChatResponse> {
  const body = await requestJson(`${CHAT_BASE}/api/chat`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ messages: trimHistory(messages) }),
    signal,
  })
  return normaliseChatResponse(body)
}

function isLineString(value: unknown): value is RouteLeg['geometry'] {
  return (
    isRecord(value) &&
    value.type === 'LineString' &&
    Array.isArray(value.coordinates) &&
    value.coordinates.every((c) => Array.isArray(c) && isFiniteNumber(c[0]) && isFiniteNumber(c[1]))
  )
}

function optionalNumber(value: unknown): number | undefined {
  return isFiniteNumber(value) ? value : undefined
}

function normaliseSteps(value: unknown): RouteStep[] | undefined {
  if (!Array.isArray(value)) return undefined
  const steps: RouteStep[] = []
  for (const s of value) {
    if (!isRecord(s) || typeof s.instruction !== 'string' || !isFiniteNumber(s.distance_m)) continue
    steps.push({ instruction: s.instruction, street: typeof s.street === 'string' ? s.street : null, distance_m: s.distance_m })
  }
  return steps
}

function normaliseLeg(value: unknown): RouteLeg {
  if (!isRecord(value) || !isLineString(value.geometry)) {
    throw new ApiError(502, 'The route response has no geometry.')
  }
  const num = (v: unknown): number => (isFiniteNumber(v) ? v : Number.NaN)
  return {
    geometry: value.geometry,
    length_m: num(value.length_m),
    duration_min: num(value.duration_min),
    mean_risk: num(value.mean_risk),
    max_risk: num(value.max_risk),
    path_risk: optionalNumber(value.path_risk),
    lit_share: optionalNumber(value.lit_share),
    main_road_share: optionalNumber(value.main_road_share),
    steps: normaliseSteps(value.steps),
  }
}

export async function fetchRoute(origin: LngLat, destination: LngLat, signal?: AbortSignal): Promise<RouteResponse> {
  const body = await requestJson(`${API_BASE}/api/route`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ origin, destination }),
    signal,
  })
  return normaliseRouteResponse(body)
}

export function normaliseRouteResponse(body: unknown): RouteResponse {
  if (!isRecord(body)) throw new ApiError(502, 'The route response is not an object.')
  return {
    fast: normaliseLeg(body.fast),
    safe: normaliseLeg(body.safe),
    risk_reduction: isFiniteNumber(body.risk_reduction) ? body.risk_reduction : undefined,
    extra_distance_m: optionalNumber(body.extra_distance_m),
    night_multiplier: optionalNumber(body.night_multiplier),
  }
}

export async function fetchHotels(lat: number, lng: number, radiusM: number, signal?: AbortSignal): Promise<Hotel[]> {
  const params = new URLSearchParams({ lat: String(lat), lng: String(lng), radius_m: String(radiusM) })
  const body = await requestJson(`${API_BASE}/api/hotels?${params}`, { signal })
  if (!isRecord(body) || !Array.isArray(body.hotels)) return []
  const hotels: Hotel[] = []
  for (const h of body.hotels) {
    if (!isRecord(h) || !isFiniteNumber(h.lat) || !isFiniteNumber(h.lng)) continue
    const risk =
      isRecord(h.risk) && isFiniteNumber(h.risk.mean_score) && isFiniteNumber(h.risk.london_percentile)
        ? { mean_score: h.risk.mean_score, london_percentile: h.risk.london_percentile }
        : null
    hotels.push({
      id: typeof h.id === 'string' ? h.id : `${h.lat},${h.lng}`,
      name: typeof h.name === 'string' && h.name ? h.name : 'Unnamed accommodation',
      lat: h.lat,
      lng: h.lng,
      distance_m: isFiniteNumber(h.distance_m) ? h.distance_m : Number.NaN,
      risk,
    })
  }
  return hotels
}
