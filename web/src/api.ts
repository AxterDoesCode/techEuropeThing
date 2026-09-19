import type { AgentStatus, CrimePoints, EventCollection, LngLat, OfficialAlert, RouteResult } from './types'

// Without VITE_API_BASE the client reads the static files written by
// `python -m backend.tools.export_sample web/public/sample`.
export const API_BASE = import.meta.env.VITE_API_BASE as string | undefined

// Base URL of POST /api/chat (docs/API.md). Undefined in sample mode: there is no chat backend.
const DEFAULT_CHAT_BASE = 'https://alexchau256--london-risk-chat.modal.run'
export const CHAT_BASE: string | undefined =
  (import.meta.env.VITE_CHAT_BASE as string | undefined) || (API_BASE ? DEFAULT_CHAT_BASE : undefined)

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}: ${url}`)
  return resp.json() as Promise<T>
}

export function fetchEvents(): Promise<EventCollection> {
  return getJson(API_BASE ? `${API_BASE}/api/events` : '/sample/events.json')
}

export function fetchAgents(): Promise<AgentStatus[]> {
  return API_BASE ? getJson(`${API_BASE}/api/agents`) : Promise.resolve([])
}

// Metropolitan Police street-level crime for the latest published month (~3 MB)
export function fetchCrimePoints(): Promise<CrimePoints> {
  return getJson(API_BASE ? `${API_BASE}/api/crime-points` : '/sample/crime_points.json')
}

export const POLL_INTERVAL_MS = 15_000
// police.uk publishes monthly
export const CRIME_REFRESH_MS = 6 * 60 * 60 * 1000

// Routing needs the backend; the static sample mode has no equivalent.
export const ROUTING_AVAILABLE = Boolean(API_BASE)

export async function fetchRoute(origin: LngLat, destination: LngLat, alpha: number): Promise<RouteResult> {
  if (!API_BASE) throw new Error('Routing needs a backend: set VITE_API_BASE')
  const resp = await fetch(`${API_BASE}/api/route`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ origin: [origin.lng, origin.lat], destination: [destination.lng, destination.lat], alpha }),
  })
  if (!resp.ok) {
    const body = (await resp.json().catch(() => null)) as { detail?: unknown } | null
    const detail = typeof body?.detail === 'string' ? body.detail : `${resp.status} ${resp.statusText}`
    throw new Error(detail)
  }
  return resp.json() as Promise<RouteResult>
}

// Official alerts (Met Office warnings, UK Emergency Alerts); the backend polls the feeds every 5 minutes
export const ALERTS_REFRESH_MS = 5 * 60 * 1000

// DEMO DATA, not real alerts. Returned only in sample mode (no VITE_API_BASE) when
// the page URL has `?demoAlerts`, so the alert banner can be shown and tested.
// `?demoAlerts=more` adds two upcoming warnings to show the "+N more" control.
function demoAlerts(more: boolean): OfficialAlert[] {
  const hours = (n: number) => new Date(Date.now() + n * 3_600_000).toISOString()
  return [
    {
      id: 'uk_emergency_alerts:demo',
      source: 'uk_emergency_alerts',
      source_label: 'GOV.UK Emergency Alerts',
      level: 'red',
      hazard: 'emergency alert',
      headline: 'Demo data: there is a very high risk of wildfires nationally.',
      url: 'https://www.gov.uk/alerts',
      starts_at: hours(-1),
      ends_at: null,
      active: true,
    },
    {
      id: 'met_office:demo',
      source: 'met_office',
      source_label: 'Met Office',
      level: 'amber',
      hazard: 'extreme heat',
      headline: 'Amber warning of extreme heat affecting London & South East England',
      url: 'https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings',
      starts_at: hours(-3),
      ends_at: hours(5),
      active: true,
    },
    ...(more ? DEMO_UPCOMING.map((a, i) => ({ ...a, starts_at: hours(20 + i), ends_at: hours(40 + i) })) : []),
  ]
}

const DEMO_UPCOMING: OfficialAlert[] = ['thunderstorm', 'rain'].map((hazard) => ({
  id: `met_office:demo-${hazard}`,
  source: 'met_office',
  source_label: 'Met Office',
  level: 'amber',
  hazard,
  headline: `Amber warning of ${hazard} affecting London & South East England`,
  url: 'https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings',
  starts_at: '',
  ends_at: null,
  active: false,
}))

export function fetchAlerts(): Promise<OfficialAlert[]> {
  if (API_BASE) return getJson(`${API_BASE}/api/alerts`)
  const params = new URLSearchParams(window.location.search)
  return Promise.resolve(params.has('demoAlerts') ? demoAlerts(params.get('demoAlerts') === 'more') : [])
}
