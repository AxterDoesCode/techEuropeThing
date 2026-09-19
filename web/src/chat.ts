import type { Feature, Geometry } from 'geojson'
import { CHAT_BASE } from './api'
import { CATEGORY_LABEL } from './map/colors'
import type { BBox, Category, EventProps, RouteLeg, RouteResult } from './types'

export type ChatRole = 'user' | 'assistant'

export interface ChatMessage {
  role: ChatRole
  content: string
}

export const MAX_MESSAGE_CHARS = 2000
// The server rejects longer conversations; older messages are left out of the request
export const MAX_MESSAGES = 30
// Events listed in one response; the contract caps `ui.events` at this number
const MAX_EVENTS = 40
const MAX_AREA_RADIUS_M = 10_000

/** Map state sent with a question so that "here" and "this event" can be resolved. */
export interface MapContext {
  center: [number, number]
  // [west, south, east, north]
  bounds: BBox
  zoom: number
  selected_event_id?: string
}

export interface ChatSource {
  title: string
  url: string
}

export interface EventRelevance {
  /** distance to the route line or to the area centre */
  distance_m: number
  /** position along the route; null for an area */
  along_m: number | null
}

export type AssistantEvent = Feature<Geometry, EventProps & { relevance?: EventRelevance }>

export interface ChatArea {
  label: string
  center: [number, number]
  radius_m: number
  bbox: BBox | null
}

export interface RouteLabels {
  origin: string
  destination: string
}

/** What a response asks the client to draw. Every field has been validated by parseUi. */
export interface ChatUi {
  intent: 'route' | 'area' | 'other'
  route: RouteResult | null
  routeLabels: RouteLabels | null
  area: ChatArea | null
  /** all relevant events, highest risk first */
  events: AssistantEvent[]
  /** ids of `events` that the answer refers to */
  highlightIds: string[]
  focus: 'route' | 'area' | null
}

export interface ChatResponse {
  answer: string
  sources: ChatSource[]
  /** null when the backend sent no `ui` (older deployment) or nothing in it was usable */
  ui: ChatUi | null
}

export class ChatError extends Error {
  /** HTTP status; null for a network failure or an unusable response body */
  readonly status: number | null
  constructor(message: string, status: number | null = null) {
    super(message)
    this.name = 'ChatError'
    this.status = status
  }
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isFiniteNumber = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const isPosition = (v: unknown): v is [number, number] =>
  Array.isArray(v) && v.length >= 2 && isFiniteNumber(v[0]) && isFiniteNumber(v[1]) && Math.abs(v[0]) <= 180 && Math.abs(v[1]) <= 90
const num = (v: unknown, fallback: number) => (isFiniteNumber(v) ? v : fallback)
const str = (v: unknown, fallback: string) => (typeof v === 'string' ? v : fallback)
const strOrNull = (v: unknown) => (typeof v === 'string' ? v : null)
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : [])

function parseBBox(v: unknown): BBox | null {
  if (!Array.isArray(v) || v.length !== 4 || !v.every(isFiniteNumber)) return null
  return [v[0], v[1], v[2], v[3]]
}

function parseLeg(raw: unknown): RouteLeg | null {
  if (!isRecord(raw) || !isRecord(raw.geometry) || !Array.isArray(raw.geometry.coordinates)) return null
  const coordinates = raw.geometry.coordinates.filter(isPosition).map((c): [number, number] => [c[0], c[1]])
  if (coordinates.length < 2) return null
  return {
    ...(raw as Partial<RouteLeg>),
    geometry: { type: 'LineString', coordinates },
    length_m: num(raw.length_m, 0),
    duration_min: num(raw.duration_min, 0),
    mean_risk: num(raw.mean_risk, 0),
    max_risk: num(raw.max_risk, 0),
  }
}

// The safe leg is required. A missing or malformed fast leg is replaced by the
// safe one, which draws as a single line.
function parseRoute(raw: unknown): RouteResult | null {
  if (!isRecord(raw)) return null
  const safe = parseLeg(raw.safe)
  if (!safe) return null
  return {
    ...(raw as Partial<RouteResult>),
    safe,
    fast: parseLeg(raw.fast) ?? safe,
    alpha: num(raw.alpha, 0),
    beta: num(raw.beta, 0),
    risk_reduction: num(raw.risk_reduction, 0),
    extra_distance_m: num(raw.extra_distance_m, 0),
    attribution: str(raw.attribution, ''),
  }
}

function parseArea(raw: unknown): ChatArea | null {
  if (!isRecord(raw) || !isPosition(raw.center) || !isFiniteNumber(raw.radius_m) || raw.radius_m <= 0) return null
  return {
    label: str(raw.label, ''),
    center: [raw.center[0], raw.center[1]],
    radius_m: Math.min(raw.radius_m, MAX_AREA_RADIUS_M),
    bbox: parseBBox(raw.bbox),
  }
}

// An event needs a geometry, an id and a position. The other properties read by
// the event popup and the markers take a neutral value when they are missing.
function parseEvent(raw: unknown): AssistantEvent | null {
  if (!isRecord(raw) || !isRecord(raw.geometry) || typeof raw.geometry.type !== 'string' || !isRecord(raw.properties)) return null
  const p = raw.properties
  if (typeof p.id !== 'string' || p.id === '' || !isPosition([p.lng, p.lat])) return null
  const relevance = isRecord(p.relevance) && isFiniteNumber(p.relevance.distance_m)
    ? { distance_m: p.relevance.distance_m, along_m: isFiniteNumber(p.relevance.along_m) ? p.relevance.along_m : null }
    : undefined
  const properties: AssistantEvent['properties'] = {
    ...(p as Partial<EventProps>),
    id: p.id,
    category: typeof p.category === 'string' && p.category in CATEGORY_LABEL ? (p.category as Category) : 'other',
    title: str(p.title, 'Untitled event'),
    summary: strOrNull(p.summary),
    lng: p.lng as number,
    lat: p.lat as number,
    radius_m: num(p.radius_m, 0),
    severity: num(p.severity, 0),
    confidence: num(p.confidence, 0),
    half_life_min: isFiniteNumber(p.half_life_min) ? p.half_life_min : null,
    occurred_at: str(p.occurred_at, ''),
    expires_at: strOrNull(p.expires_at),
    ended_at: strOrNull(p.ended_at),
    source_ids: strings(p.source_ids),
    urls: strings(p.urls).filter(isHttpUrl),
    risk: num(p.risk, 0),
    subtype: strOrNull(p.subtype),
    relevance,
  }
  return { type: 'Feature', geometry: raw.geometry as unknown as Geometry, properties }
}

function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url)
}

/** Validates `ui` of a chat response. Malformed parts are dropped; null when nothing usable remains. */
export function parseUi(raw: unknown): ChatUi | null {
  if (!isRecord(raw)) return null
  const seen = new Set<string>()
  const events = (Array.isArray(raw.events) ? raw.events : [])
    .map(parseEvent)
    .filter((e): e is AssistantEvent => {
      if (!e || seen.has(e.properties.id)) return false
      seen.add(e.properties.id)
      return true
    })
    .slice(0, MAX_EVENTS)
  const ids = new Set(events.map((e) => e.properties.id))
  const route = parseRoute(raw.route)
  const area = parseArea(raw.area)
  if (!route && !area && events.length === 0) return null
  const labels = raw.route_labels
  const focus = raw.focus === 'route' && route ? 'route' : raw.focus === 'area' && area ? 'area' : null
  return {
    intent: raw.intent === 'route' || raw.intent === 'area' ? raw.intent : 'other',
    route,
    routeLabels: route && isRecord(labels) ? { origin: str(labels.origin, ''), destination: str(labels.destination, '') } : null,
    area,
    events,
    highlightIds: [...new Set(strings(raw.highlight_event_ids))].filter((id) => ids.has(id)),
    focus,
  }
}

export function parseChatResponse(raw: unknown): ChatResponse {
  if (!isRecord(raw) || typeof raw.answer !== 'string') throw new ChatError('The chat service returned an unexpected response.')
  const sources = (Array.isArray(raw.sources) ? raw.sources : []).flatMap((s): ChatSource[] =>
    isRecord(s) && typeof s.url === 'string' && isHttpUrl(s.url) ? [{ title: str(s.title, s.url) || s.url, url: s.url }] : [],
  )
  return { answer: raw.answer, sources, ui: parseUi(raw.ui) }
}

const demoChatRequested = () => new URLSearchParams(window.location.search).has('demoChat')

/** Chat needs CHAT_BASE, or `?demoChat` in sample mode (canned responses from chatDemo.ts). */
export const CHAT_MODE: 'live' | 'demo' | 'unavailable' = CHAT_BASE ? 'live' : demoChatRequested() ? 'demo' : 'unavailable'

function errorText(status: number, detail: string | null): string {
  if (status === 429) return `Too many questions: the limit is 20 per 10 minutes. Wait a few minutes and send it again.${detail ? ` (${detail})` : ''}`
  if (status === 503) return `The chat service is not configured on the server.${detail ? ` (${detail})` : ''}`
  return detail ?? `The chat request failed with status ${status}.`
}

/** `messages` is the whole conversation, the last one from the user. Rejects with ChatError, or AbortError when `signal` aborts. */
export async function sendChat(messages: ChatMessage[], context: MapContext | null, signal: AbortSignal): Promise<ChatResponse> {
  const recent = messages.slice(-MAX_MESSAGES)
  // A conversation cut to the last MAX_MESSAGES can start with an answer; the first message sent is a question
  const trimmed = recent[0]?.role === 'assistant' ? recent.slice(1) : recent
  if (CHAT_MODE === 'unavailable') {
    throw new ChatError('Chat is not available in sample mode: start the page with VITE_API_BASE set, or add ?demoChat to the URL for canned demo answers.')
  }
  if (CHAT_MODE === 'demo') {
    const { demoChatResponse } = await import('./chatDemo')
    return parseChatResponse(await demoChatResponse(trimmed, signal))
  }
  let resp: Response
  try {
    resp = await fetch(`${CHAT_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: trimmed, ...(context ? { context } : {}) }),
      signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ChatError('The chat service could not be reached.')
  }
  if (!resp.ok) {
    const body = (await resp.json().catch(() => null)) as { detail?: unknown } | null
    throw new ChatError(errorText(resp.status, typeof body?.detail === 'string' ? body.detail : null), resp.status)
  }
  return parseChatResponse(await resp.json().catch(() => null))
}
