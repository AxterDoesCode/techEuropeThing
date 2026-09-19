// DEMO DATA, not answers of the chat backend. Used only in sample mode (no
// VITE_API_BASE) when the page URL has `?demoChat`, so the chat panel and the
// assistant map layer can be shown and tested. The responses have the JSON shape of
// POST /api/chat and pass through the same validation as a server response.
import { fetchEvents } from './api'
import { bboxAround, distanceM, closestOnLine } from './geo'
import type { ChatMessage } from './chat'
import type { EventFeature } from './types'

type Position = [number, number]

const DEMO_DELAY_MS = 1500
const DEMO_ROUTE_EVENTS = 8
const DEMO_ROUTE_HIGHLIGHTS = 7
const DEMO_AREA_MIN_EVENTS = 5
const DEMO_AREA_HIGHLIGHTS = 3
// Several sample events share the position of a station; the demo route uses events at distinct positions
const DEMO_MIN_SPACING_M = 60

// Bank station to Farringdon station: the part of the bundled sample with the most events
const DEMO_ROUTE_LABELS = { origin: 'Bank station (demo)', destination: 'Farringdon station (demo)' }
const DEMO_SAFE_LINE: Position[] = [
  [-0.0886, 51.5133], [-0.0915, 51.5141], [-0.0938, 51.5153], [-0.0957, 51.5171],
  [-0.0985, 51.5181], [-0.1012, 51.5187], [-0.1034, 51.5196], [-0.105, 51.5203],
]
const DEMO_FAST_LINE: Position[] = [
  [-0.0886, 51.5133], [-0.0921, 51.5147], [-0.0962, 51.516], [-0.0998, 51.5175], [-0.1029, 51.519], [-0.105, 51.5203],
]
const DEMO_AREA = { label: 'Bethnal Green (demo)', center: [-0.0553, 51.527] as Position, radius_m: 600 }

function lineLengthM(line: Position[]): number {
  return line.slice(1).reduce((sum, p, i) => sum + distanceM(line[i], p), 0)
}

function demoLeg(line: Position[], meanRisk: number, maxRisk: number, litShare: number, mainRoadShare: number) {
  const length = lineLengthM(line)
  return {
    geometry: { type: 'LineString', coordinates: line },
    length_m: Math.round(length),
    duration_min: length / 80,
    mean_risk: meanRisk,
    max_risk: maxRisk,
    path_risk: Number((1 - Math.exp((-length * meanRisk) / 720)).toFixed(3)),
    lit_share: litShare,
    main_road_share: mainRoadShare,
    park_m: 0,
    underpass_m: 0,
    steps: [],
  }
}

function demoRouteBody() {
  const fast = demoLeg(DEMO_FAST_LINE, 0.31, 0.62, 0.81, 0.35)
  const safe = demoLeg(DEMO_SAFE_LINE, 0.24, 0.55, 0.94, 0.72)
  return {
    fast,
    safe,
    alpha: 4,
    beta: 1,
    gamma: 0.5,
    night_multiplier: 1,
    risk_reduction: Number((1 - safe.mean_risk / fast.mean_risk).toFixed(3)),
    extra_distance_m: safe.length_m - fast.length_m,
    attribution: 'Demo route: fabricated in the client, not computed on the walking network.',
  }
}

const position = (e: EventFeature): Position => [e.properties.lng, e.properties.lat]

function withRelevance(e: EventFeature, relevance: { distance_m: number; along_m: number | null }) {
  return { ...e, properties: { ...e.properties, relevance } }
}

const byRisk = (a: EventFeature, b: EventFeature) => b.properties.risk - a.properties.risk

function demoRouteResponse(sample: EventFeature[]) {
  const spaced: EventFeature[] = []
  const events = sample
    .map((e) => ({ e, rel: closestOnLine(position(e), DEMO_SAFE_LINE) }))
    .sort((a, b) => a.rel.distance_m - b.rel.distance_m)
    .filter(({ e }) => {
      if (spaced.some((other) => distanceM(position(other), position(e)) < DEMO_MIN_SPACING_M)) return false
      spaced.push(e)
      return true
    })
    .slice(0, DEMO_ROUTE_EVENTS)
    .map(({ e, rel }) => withRelevance(e, { distance_m: Math.round(rel.distance_m), along_m: Math.round(rel.along_m) }))
    .sort(byRisk)
  const route = demoRouteBody()
  return {
    answer:
      `Demo answer (not from the assistant). Walking route from ${DEMO_ROUTE_LABELS.origin} to ${DEMO_ROUTE_LABELS.destination}: ` +
      `the lower-risk route is ${route.safe.length_m} m, ${route.extra_distance_m} m longer than the shortest one, with a mean modelled risk of ` +
      `${route.safe.mean_risk} against ${route.fast.mean_risk}.\n\n${events.length} current events lie near the route; ` +
      `${Math.min(DEMO_ROUTE_HIGHLIGHTS, events.length)} of them are highlighted on the map.`,
    sources: [{ title: 'Demo source: TfL road disruptions', url: 'https://tfl.gov.uk/traffic/status/' }],
    places: [],
    tool_calls: ['demo_route()'],
    ui: {
      intent: 'route',
      route,
      route_labels: DEMO_ROUTE_LABELS,
      area: null,
      events,
      highlight_event_ids: events.slice(0, DEMO_ROUTE_HIGHLIGHTS).map((e) => e.properties.id),
      focus: 'route',
    },
  }
}

function demoAreaResponse(sample: EventFeature[]) {
  const ranked = sample
    .map((e) => ({ e, d: distanceM(DEMO_AREA.center, position(e)) }))
    .sort((a, b) => a.d - b.d)
  const inside = ranked.filter((r) => r.d <= DEMO_AREA.radius_m)
  const events = (inside.length >= DEMO_AREA_MIN_EVENTS ? inside : ranked.slice(0, DEMO_AREA_MIN_EVENTS))
    .map(({ e, d }) => withRelevance(e, { distance_m: Math.round(d), along_m: null }))
    .sort(byRisk)
  return {
    answer:
      `Demo answer (not from the assistant). ${DEMO_AREA.label}, ${DEMO_AREA.radius_m} m radius: ${inside.length} current ` +
      `event${inside.length === 1 ? '' : 's'} inside the circle. The list below holds the ${events.length} nearest events; ` +
      `the ${Math.min(DEMO_AREA_HIGHLIGHTS, events.length)} with the highest current risk are highlighted on the map.`,
    sources: [{ title: 'Demo source: data.police.uk', url: 'https://data.police.uk/' }],
    places: [],
    tool_calls: ['demo_area()'],
    ui: {
      intent: 'area',
      route: null,
      route_labels: null,
      area: { ...DEMO_AREA, bbox: bboxAround(DEMO_AREA.center, DEMO_AREA.radius_m) },
      events,
      highlight_event_ids: events.slice(0, DEMO_AREA_HIGHLIGHTS).map((e) => e.properties.id),
      focus: 'area',
    },
  }
}

function demoDelay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort)
      resolve()
    }, DEMO_DELAY_MS)
    if (signal.aborted) abort()
    else signal.addEventListener('abort', abort, { once: true })
  })
}

/** Canned response: a route for a question containing "route" or "walk", an area report otherwise. */
export async function demoChatResponse(messages: ChatMessage[], signal: AbortSignal): Promise<unknown> {
  await demoDelay(signal)
  const sample = (await fetchEvents()).features.filter((e) => e.properties.ended_at === null)
  const question = messages[messages.length - 1]?.content ?? ''
  return /route|walk/i.test(question) ? demoRouteResponse(sample) : demoAreaResponse(sample)
}
