// Parser for the `tool_calls` strings of a chat response. The server formats
// them with Python f-strings (backend/chat.py), for example:
//   find_place('Brixton')            find_place("King's Cross")
//   area_report(51.4627, -0.1145, 500)
//   walking_route((51.5324, -0.1230) -> (51.5100, -0.1303))
//   hotels_near(51.5154, -0.1755, 1500)
// Anything that does not match one of these forms parses to null.

import type { Place } from './api'

export interface LatLng {
  lat: number
  lng: number
}

export type ToolCall =
  | { tool: 'find_place'; query: string }
  | { tool: 'area_report'; lat: number; lng: number; radiusM: number }
  | { tool: 'hotels_near'; lat: number; lng: number; radiusM: number }
  | { tool: 'walking_route'; from: LatLng; to: LatLng }

const NUM = '([-+]?\\d+(?:\\.\\d+)?)'
const CIRCLE_RE = new RegExp(`^(area_report|hotels_near)\\(\\s*${NUM}\\s*,\\s*${NUM}\\s*,\\s*${NUM}\\s*\\)$`)
const ROUTE_RE = new RegExp(
  `^walking_route\\(\\s*\\(\\s*${NUM}\\s*,\\s*${NUM}\\s*\\)\\s*->\\s*\\(\\s*${NUM}\\s*,\\s*${NUM}\\s*\\)\\s*\\)$`,
)
const PLACE_RE = /^find_place\(([\s\S]*)\)$/

function validLatLng(lat: number, lng: number): boolean {
  return Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180
}

// Reads a Python string literal as produced by repr(): single or double
// quotes, with backslash escapes for the quote character and the backslash.
function parsePythonString(literal: string): string | null {
  const text = literal.trim()
  if (text.length < 2) return null
  const quote = text[0]
  if ((quote !== "'" && quote !== '"') || text[text.length - 1] !== quote) return null
  let out = ''
  const inner = text.slice(1, -1)
  for (let i = 0; i < inner.length; i++) {
    const ch = inner[i]
    if (ch === quote) return null
    if (ch === '\\') {
      const next = inner[i + 1]
      if (next === undefined) return null
      out += next === 'n' || next === 't' ? ' ' : next
      i++
    } else {
      out += ch
    }
  }
  return out
}

export function parseToolCall(value: unknown): ToolCall | null {
  if (typeof value !== 'string') return null
  const text = value.trim()

  const circle = CIRCLE_RE.exec(text)
  if (circle) {
    const lat = Number(circle[2])
    const lng = Number(circle[3])
    const radiusM = Number(circle[4])
    if (!validLatLng(lat, lng) || !(radiusM > 0) || radiusM > 100_000) return null
    return { tool: circle[1] === 'area_report' ? 'area_report' : 'hotels_near', lat, lng, radiusM }
  }

  const route = ROUTE_RE.exec(text)
  if (route) {
    const [fromLat, fromLng, toLat, toLng] = route.slice(1, 5).map(Number)
    if (!validLatLng(fromLat, fromLng) || !validLatLng(toLat, toLng)) return null
    return { tool: 'walking_route', from: { lat: fromLat, lng: fromLng }, to: { lat: toLat, lng: toLng } }
  }

  const place = PLACE_RE.exec(text)
  if (place) {
    const query = parsePythonString(place[1])
    if (query === null || query.trim() === '') return null
    return { tool: 'find_place', query }
  }

  return null
}

export function parseToolCalls(values: unknown): ToolCall[] {
  if (!Array.isArray(values)) return []
  const calls: ToolCall[] = []
  for (const v of values) {
    const call = parseToolCall(v)
    if (call) calls.push(call)
  }
  return calls
}

// Tool calls print coordinates with 4 decimals (about 11 m), so a place whose
// coordinates differ by less than 0.0002 degrees is taken to be the same point.
function placeNameAt(point: LatLng, places: Place[]): string | null {
  const match = places.find((p) => Math.abs(p.lat - point.lat) < 0.0002 && Math.abs(p.lng - point.lng) < 0.0002)
  return match ? match.query : null
}

function pointText(point: LatLng, places: Place[]): string {
  return placeNameAt(point, places) ?? `${point.lat.toFixed(4)}, ${point.lng.toFixed(4)}`
}

// Plain-language description of one step for the "How this was answered" list.
export function describeToolCall(call: ToolCall, places: Place[]): string {
  switch (call.tool) {
    case 'find_place': {
      const found = places.some((p) => p.query === call.query && p.found)
      return found ? `Looked up “${call.query}”` : `Looked up “${call.query}” (no match found)`
    }
    case 'area_report':
      return `Read area data within ${call.radiusM} m of ${pointText(call, places)}`
    case 'hotels_near':
      return `Searched hotels within ${call.radiusM} m of ${pointText(call, places)}`
    case 'walking_route':
      return `Compared walking routes from ${pointText(call.from, places)} to ${pointText(call.to, places)}`
  }
}
