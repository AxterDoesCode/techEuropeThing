import type { BBox } from './types'

type Position = [number, number]

const EARTH_RADIUS_M = 6_371_000
const RAD = Math.PI / 180

// Bounds accepted for a camera fit that comes from a chat response: Greater London
// (docs/API.md) with a margin. Coordinates outside are clamped to it.
const FIT_LIMIT: BBox = [-0.8, 51.1, 0.6, 51.9]

// Local planar coordinates in metres relative to `origin`; adequate over a few kilometres
function toMetres(p: Position, origin: Position): Position {
  return [(p[0] - origin[0]) * RAD * EARTH_RADIUS_M * Math.cos(origin[1] * RAD), (p[1] - origin[1]) * RAD * EARTH_RADIUS_M]
}

export function distanceM(a: Position, b: Position): number {
  const [x, y] = toMetres(b, a)
  return Math.hypot(x, y)
}

/** Distance from `p` to a polyline, and the length along the line at the closest position. */
export function distanceToLineM(p: Position, line: Position[]): { distance_m: number; along_m: number } {
  let best = { distance_m: Infinity, along_m: 0 }
  let travelled = 0
  for (let i = 1; i < line.length; i++) {
    const a = line[i - 1]
    const [px, py] = toMetres(p, a)
    const [bx, by] = toMetres(line[i], a)
    const length = Math.hypot(bx, by)
    const t = length === 0 ? 0 : Math.min(1, Math.max(0, (px * bx + py * by) / (length * length)))
    const d = Math.hypot(px - t * bx, py - t * by)
    if (d < best.distance_m) best = { distance_m: d, along_m: travelled + t * length }
    travelled += length
  }
  return best
}

export function bboxAround(center: Position, radiusM: number): BBox {
  const dLat = radiusM / (EARTH_RADIUS_M * RAD)
  const dLng = dLat / Math.cos(center[1] * RAD)
  return [center[0] - dLng, center[1] - dLat, center[0] + dLng, center[1] + dLat]
}

export function bboxOf(coords: Position[]): BBox | null {
  if (coords.length === 0) return null
  const lngs = coords.map((c) => c[0])
  const lats = coords.map((c) => c[1])
  return [Math.min(...lngs), Math.min(...lats), Math.max(...lngs), Math.max(...lats)]
}

/** The part of `bbox` inside FIT_LIMIT, or null when it is not finite or lies outside. */
export function clampFitBounds(bbox: BBox): BBox | null {
  if (!bbox.every(Number.isFinite)) return null
  const west = Math.max(FIT_LIMIT[0], Math.min(bbox[0], bbox[2]))
  const south = Math.max(FIT_LIMIT[1], Math.min(bbox[1], bbox[3]))
  const east = Math.min(FIT_LIMIT[2], Math.max(bbox[0], bbox[2]))
  const north = Math.min(FIT_LIMIT[3], Math.max(bbox[1], bbox[3]))
  return west <= east && south <= north ? [west, south, east, north] : null
}
