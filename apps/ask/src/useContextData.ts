// Loads the geometry that a chat response does not contain: the two walking
// routes for each walking_route call (unless the response included the route
// in `ui.route`) and the hotels for each hotels_near call.

import { useEffect, useMemo, useState } from 'react'
import { fetchHotels, fetchRoute } from './api'
import type { Hotel, RouteResponse } from './api'
import type { MapContext } from './mapContext'

// The chat tool passes the first 8 hotels of the default ordering to the model.
const HOTELS_SHOWN = 8

export interface ContextData {
  key: string
  routes: RouteResponse[]
  hotels: Hotel[]
  failed: number
}

export type LoadState = 'idle' | 'loading' | 'done'

const cache = new Map<string, ContextData>()

export function useContextData(context: MapContext | null): { data: ContextData | null; state: LoadState } {
  const [data, setData] = useState<ContextData | null>(null)

  useEffect(() => {
    if (!context || (context.routes.length === 0 && context.hotelSearches.length === 0)) return
    if (cache.has(context.key)) return
    const controller = new AbortController()
    const routeJobs = context.routes.map((r) =>
      fetchRoute([r.from.lng, r.from.lat], [r.to.lng, r.to.lat], controller.signal),
    )
    const hotelJobs = context.hotelSearches.map((h) => fetchHotels(h.lat, h.lng, h.radiusM, controller.signal))
    void Promise.all([Promise.allSettled(routeJobs), Promise.allSettled(hotelJobs)]).then(([routes, hotels]) => {
      if (controller.signal.aborted) return
      const loaded: ContextData = { key: context.key, routes: [], hotels: [], failed: 0 }
      for (const r of routes) {
        if (r.status === 'fulfilled') loaded.routes.push(r.value)
        else loaded.failed++
      }
      for (const h of hotels) {
        if (h.status === 'fulfilled') loaded.hotels.push(...h.value.slice(0, HOTELS_SHOWN))
        else loaded.failed++
      }
      cache.set(context.key, loaded)
      setData(loaded)
    })
    return () => controller.abort()
  }, [context])

  // Memoised so that the map effects run only when the drawn data changes.
  return useMemo<{ data: ContextData | null; state: LoadState }>(() => {
    if (!context) return { data: null, state: 'idle' }
    const included = context.includedRoute ? [context.includedRoute] : []
    if (context.routes.length === 0 && context.hotelSearches.length === 0) {
      if (included.length === 0) return { data: null, state: 'idle' }
      return { data: { key: context.key, routes: included, hotels: [], failed: 0 }, state: 'done' }
    }
    const loaded = data && data.key === context.key ? data : (cache.get(context.key) ?? null)
    if (!loaded) return { data: null, state: 'loading' }
    return { data: included.length > 0 ? { ...loaded, routes: [...included, ...loaded.routes] } : loaded, state: 'done' }
  }, [context, data])
}
