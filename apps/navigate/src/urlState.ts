// Route inputs kept in the query string: from, to ("lng,lat"), from_name, to_name, alpha, night.
import type { LngLat } from './api'
import { parseLngLat } from './geo'

export interface Endpoint {
  point: LngLat
  name: string
}

export interface RouteInputs {
  origin: Endpoint | null
  destination: Endpoint | null
  alpha: number
  night: boolean
}

export const DEFAULT_ALPHA = 4

export const coordinateLabel = ([lng, lat]: LngLat): string => `${lat.toFixed(5)}, ${lng.toFixed(5)}`

function readEndpoint(params: URLSearchParams, key: 'from' | 'to'): Endpoint | null {
  const point = parseLngLat(params.get(key))
  if (!point) return null
  return { point, name: params.get(`${key}_name`)?.trim() || coordinateLabel(point) }
}

// Without a `night` parameter the toggle follows the local clock: on from 19:00 to 05:59.
function defaultNight(now: Date): boolean {
  const hour = now.getHours()
  return hour >= 19 || hour < 6
}

export function readUrl(search: string, now: Date = new Date()): RouteInputs {
  const params = new URLSearchParams(search)
  const alpha = Number(params.get('alpha'))
  const night = params.get('night')
  return {
    origin: readEndpoint(params, 'from'),
    destination: readEndpoint(params, 'to'),
    alpha: params.has('alpha') && Number.isFinite(alpha) ? Math.min(10, Math.max(0, alpha)) : DEFAULT_ALPHA,
    night: night === null ? defaultNight(now) : night === '1' || night === 'true',
  }
}

export function writeUrl(inputs: RouteInputs): void {
  const params = new URLSearchParams()
  const put = (key: 'from' | 'to', endpoint: Endpoint | null) => {
    if (!endpoint) return
    params.set(key, `${endpoint.point[0].toFixed(5)},${endpoint.point[1].toFixed(5)}`)
    if (endpoint.name !== coordinateLabel(endpoint.point)) params.set(`${key}_name`, endpoint.name)
  }
  put('from', inputs.origin)
  put('to', inputs.destination)
  params.set('alpha', String(inputs.alpha))
  params.set('night', inputs.night ? '1' : '0')
  const next = `${window.location.pathname}?${params}`
  if (next !== window.location.pathname + window.location.search) window.history.replaceState(null, '', next)
}
