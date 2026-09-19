import type { AgentStatus, CrimePoints, EventCollection } from './types'

// Without VITE_API_BASE the client reads the static files written by
// `python -m backend.tools.export_sample web/public/sample`.
const API_BASE = import.meta.env.VITE_API_BASE as string | undefined

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
