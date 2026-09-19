import { useCallback, useMemo, useState } from 'react'
import { formatCoord } from './format'
import type { LngLat, RouteEndpoint, RouteResult } from './types'

export type RouteFields = Record<RouteEndpoint, string>

// Accepts "lng, lat" (GeoJSON order, as shown in the Position panel)
export function parsePoint(text: string): LngLat | null {
  const parts = text.split(/[,\s]+/).filter(Boolean)
  if (parts.length !== 2) return null
  const [lng, lat] = parts.map(Number)
  if (!Number.isFinite(lng) || !Number.isFinite(lat) || Math.abs(lng) > 180 || Math.abs(lat) > 90) return null
  return { lng, lat }
}

// Route request state shared by the panel (fields, picking) and the map (markers, paths)
export function useRouteState() {
  const [fields, setFieldsState] = useState<RouteFields>({ origin: '', destination: '' })
  const [picking, setPicking] = useState<RouteEndpoint | null>(null)
  const [route, setRoute] = useState<RouteResult | null>(null)

  // A computed route belongs to the endpoints it was requested for
  const setFields = useCallback((next: RouteFields) => {
    setFieldsState(next)
    setRoute(null)
  }, [])

  // Returns true when the click was used to set an endpoint
  const pick = useCallback(
    (pos: LngLat): boolean => {
      if (!picking) return false
      const text = `${formatCoord(pos.lng)}, ${formatCoord(pos.lat)}`
      const next = { ...fields, [picking]: text }
      setFieldsState(next)
      // after the origin, continue with the destination if it is still empty
      setPicking(picking === 'origin' && !next.destination ? 'destination' : null)
      setRoute(null)
      return true
    },
    [picking, fields],
  )

  const origin = useMemo(() => parsePoint(fields.origin), [fields.origin])
  const destination = useMemo(() => parsePoint(fields.destination), [fields.destination])
  return { fields, setFields, picking, setPicking, route, setRoute, origin, destination, pick }
}
