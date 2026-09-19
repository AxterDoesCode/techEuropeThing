// Derives what the context map draws from one chat response.

import type { ChatResponse, LngLat, Place, RouteResponse } from './api'
import { parseToolCalls } from './toolCalls'
import type { LatLng } from './toolCalls'

export interface Circle {
  lat: number
  lng: number
  radiusM: number
}

export interface MapContext {
  key: string
  places: Place[]
  areas: Circle[]
  hotelSearches: Circle[]
  // Route comparisons that have to be requested from /api/route.
  routes: { from: LatLng; to: LatLng }[]
  // Route comparison contained in the chat response (`ui.route`). When it is
  // present, `routes` stays empty and no route request is made.
  includedRoute: RouteResponse | null
}

export function buildMapContext(key: string, response: ChatResponse): MapContext {
  const context: MapContext = {
    key,
    places: response.places.filter((p) => p.found),
    areas: [],
    hotelSearches: [],
    routes: [],
    includedRoute: response.route ?? null,
  }
  for (const call of parseToolCalls(response.tool_calls)) {
    if (call.tool === 'area_report') context.areas.push({ lat: call.lat, lng: call.lng, radiusM: call.radiusM })
    else if (call.tool === 'hotels_near') context.hotelSearches.push({ lat: call.lat, lng: call.lng, radiusM: call.radiusM })
    else if (call.tool === 'walking_route' && !context.includedRoute) context.routes.push({ from: call.from, to: call.to })
  }
  return context
}

export function isEmptyContext(context: MapContext): boolean {
  return (
    context.places.length === 0 &&
    context.areas.length === 0 &&
    context.hotelSearches.length === 0 &&
    context.routes.length === 0 &&
    context.includedRoute === null
  )
}

export function summariseContext(context: MapContext | null): string {
  if (!context || isEmptyContext(context)) return 'nothing to show yet'
  const parts: string[] = []
  const count = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
  if (context.places.length) parts.push(count(context.places.length, 'place', 'places'))
  if (context.areas.length) parts.push(count(context.areas.length, 'area', 'areas'))
  const routeCount = context.routes.length + (context.includedRoute ? 1 : 0)
  if (routeCount) parts.push(count(routeCount, 'route comparison', 'route comparisons'))
  if (context.hotelSearches.length) parts.push(count(context.hotelSearches.length, 'hotel search', 'hotel searches'))
  return parts.join(', ')
}

// Polygon ring approximating a circle on the ground, in [lng, lat] order.
export function circleRing(circle: Circle, steps = 64): LngLat[] {
  const ring: LngLat[] = []
  const dLat = circle.radiusM / 111_320
  const dLng = dLat / Math.cos((circle.lat * Math.PI) / 180)
  for (let i = 0; i <= steps; i++) {
    const angle = (i / steps) * 2 * Math.PI
    ring.push([circle.lng + dLng * Math.cos(angle), circle.lat + dLat * Math.sin(angle)])
  }
  return ring
}
