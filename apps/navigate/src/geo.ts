import type { LngLat } from './api'

// Greater London: the scope of the platform and of place search.
export const LONDON = { west: -0.5104, south: 51.2868, east: 0.334, north: 51.6919 }
export const LONDON_CENTRE: LngLat = [-0.1278, 51.5074]

export const inLondon = ([lng, lat]: LngLat): boolean =>
  lng >= LONDON.west && lng <= LONDON.east && lat >= LONDON.south && lat <= LONDON.north

const EARTH_RADIUS_M = 6371008.8
const rad = (deg: number) => (deg * Math.PI) / 180

export function haversineM(a: LngLat, b: LngLat): number {
  const dLat = rad(b[1] - a[1])
  const dLng = rad(b[0] - a[0])
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLng / 2) ** 2
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h))
}

export interface SamplePoint {
  point: LngLat
  // Distance from the start of the line, metres.
  along_m: number
}

// `count` points along a line: the first vertex, the last vertex, and the rest at equal distances between them.
export function sampleLine(coords: LngLat[], count: number): SamplePoint[] {
  if (coords.length === 0) return []
  const cumulative = [0]
  for (let i = 1; i < coords.length; i++) cumulative.push(cumulative[i - 1] + haversineM(coords[i - 1], coords[i]))
  const total = cumulative[cumulative.length - 1]
  if (total === 0 || count < 2) return [{ point: coords[0], along_m: 0 }]

  const out: SamplePoint[] = []
  let segment = 1
  for (let k = 0; k < count; k++) {
    const target = (total * k) / (count - 1)
    while (segment < coords.length - 1 && cumulative[segment] < target) segment++
    const span = cumulative[segment] - cumulative[segment - 1]
    const t = span === 0 ? 0 : (target - cumulative[segment - 1]) / span
    const a = coords[segment - 1]
    const b = coords[segment]
    out.push({ point: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], along_m: target })
  }
  return out
}

export function bounds(coords: LngLat[]): [LngLat, LngLat] {
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity
  for (const [lng, lat] of coords) {
    west = Math.min(west, lng)
    east = Math.max(east, lng)
    south = Math.min(south, lat)
    north = Math.max(north, lat)
  }
  return [[west, south], [east, north]]
}

// London longitudes and latitudes do not overlap, so "a,b" is accepted in either order.
export function parseLngLat(text: string | null): LngLat | null {
  if (!text) return null
  const match = /^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$/.exec(text)
  if (!match) return null
  const a = Number(match[1])
  const b = Number(match[2])
  if (inLondon([a, b])) return [a, b]
  if (inLondon([b, a])) return [b, a]
  return null
}
