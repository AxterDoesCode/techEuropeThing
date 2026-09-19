import { useEffect, useState } from 'react'
import { fetchArea, isAbort, type AreaRisk, type EventFeature, type LngLat, type RouteLeg } from './api'
import { sampleLine } from './geo'

const RADIUS_M = 250

export interface NearbyEvent {
  id: string
  title: string
  category: string
  // Smallest distance to any sampled point, metres.
  distance_m: number | null
  source_ids: string[]
  url: string | null
}

export interface SampledArea {
  risk: AreaRisk
  along_m: number
  position: 'start' | 'end' | 'between'
}

export interface AlongRoute {
  events: NearbyEvent[]
  highest: SampledArea | null
  sampled: number
  failed: number
}

export type AlongState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; data: AlongRoute }
  | { status: 'error'; message: string }

// Results per route geometry, kept for the lifetime of the page.
const cache = new Map<string, AlongRoute>()

function keyOf(leg: RouteLeg): string {
  const c = leg.geometry.coordinates
  const mid = c[Math.floor(c.length / 2)]
  return [c.length, leg.length_m, c[0], mid, c[c.length - 1]].join('|')
}

const sampleCount = (lengthM: number): number => (lengthM < 800 ? 3 : lengthM < 2000 ? 4 : 5)

function toNearby(feature: EventFeature): NearbyEvent | null {
  const p = feature.properties
  const id = p?.id ?? (feature.id === undefined ? null : String(feature.id))
  if (!p || !id) return null
  return {
    id,
    title: p.title,
    category: p.category,
    distance_m: typeof p.distance_m === 'number' ? p.distance_m : null,
    source_ids: p.source_ids ?? [],
    url: p.urls?.find((u) => /^https?:\/\//.test(u)) ?? null,
  }
}

async function load(leg: RouteLeg, signal: AbortSignal): Promise<AlongRoute> {
  const coords = leg.geometry.coordinates as LngLat[]
  const samples = sampleLine(coords, sampleCount(leg.length_m))
  const settled = await Promise.allSettled(samples.map((s) => fetchArea(s.point, RADIUS_M, signal)))
  if (signal.aborted) throw new DOMException('aborted', 'AbortError')

  const events = new Map<string, NearbyEvent>()
  let highest: SampledArea | null = null
  let failed = 0
  let firstError: unknown = null
  for (let i = 0; i < settled.length; i++) {
    const result = settled[i]
    if (result.status === 'rejected') {
      failed++
      firstError ??= result.reason
      continue
    }
    const area = result.value
    if (area.risk && (!highest || area.risk.mean_score > highest.risk.mean_score)) {
      highest = {
        risk: area.risk,
        along_m: samples[i].along_m,
        position: i === 0 ? 'start' : i === samples.length - 1 ? 'end' : 'between',
      }
    }
    for (const feature of area.events ?? []) {
      const event = toNearby(feature)
      if (!event) continue
      const known = events.get(event.id)
      if (!known || (event.distance_m ?? Infinity) < (known.distance_m ?? Infinity)) events.set(event.id, event)
    }
  }
  if (failed === samples.length) throw firstError instanceof Error ? firstError : new Error('Area requests failed')

  return {
    events: [...events.values()].sort((a, b) => (a.distance_m ?? Infinity) - (b.distance_m ?? Infinity)),
    highest,
    sampled: samples.length,
    failed,
  }
}

export function useAlongRoute(leg: RouteLeg | null, attempt: number): AlongState {
  const [state, setState] = useState<AlongState>({ status: 'idle' })

  useEffect(() => {
    if (!leg) {
      setState({ status: 'idle' })
      return
    }
    const key = keyOf(leg)
    const cached = cache.get(key)
    if (cached) {
      setState({ status: 'ready', data: cached })
      return
    }
    const controller = new AbortController()
    setState({ status: 'loading' })
    load(leg, controller.signal)
      .then((data) => {
        // Partial results are not cached, so a retry requests the failed points again.
        if (data.failed === 0) cache.set(key, data)
        setState({ status: 'ready', data })
      })
      .catch((e: unknown) => {
        if (isAbort(e) || controller.signal.aborted) return
        setState({ status: 'error', message: e instanceof Error ? e.message : String(e) })
      })
    return () => controller.abort()
  }, [leg, attempt])

  return state
}
