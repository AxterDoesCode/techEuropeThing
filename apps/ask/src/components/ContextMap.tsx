import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import type { GeoJSONSource, Map as MapLibreMap, Marker } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection } from 'geojson'
import type { Hotel, RouteResponse } from '../api'
import { circleRing, isEmptyContext } from '../mapContext'
import type { MapContext } from '../mapContext'
import { useContextData } from '../useContextData'
import type { ContextData } from '../useContextData'
import { usePrefersDark } from '../usePrefersDark'
import { MapLegend } from './MapLegend'

const STYLE_LIGHT = 'https://tiles.openfreemap.org/styles/positron'
const STYLE_DARK = 'https://tiles.openfreemap.org/styles/dark'
const LONDON_CENTER: [number, number] = [-0.1276, 51.5072]

const COLORS = {
  light: { area: '#0f6e84', lower: '#1d5fd1', shortest: '#7b8088', hotel: '#8a4fbf', casing: '#ffffff' },
  dark: { area: '#5cc4da', lower: '#6ea8ff', shortest: '#9aa0a8', hotel: '#c79bf0', casing: '#101418' },
}

function collection(features: Feature[]): FeatureCollection {
  return { type: 'FeatureCollection', features }
}

function areaFeatures(context: MapContext | null): Feature[] {
  if (!context) return []
  const polygon = (kind: string, ring: [number, number][]): Feature => ({
    type: 'Feature',
    properties: { kind },
    geometry: { type: 'Polygon', coordinates: [ring] },
  })
  return [
    ...context.areas.map((c) => polygon('area', circleRing(c))),
    ...context.hotelSearches.map((c) => polygon('hotels', circleRing(c))),
  ]
}

function routeFeatures(routes: RouteResponse[], leg: 'fast' | 'safe'): Feature[] {
  return routes.map((r) => ({ type: 'Feature', properties: {}, geometry: r[leg].geometry }))
}

function hotelFeatures(hotels: Hotel[]): Feature[] {
  return hotels.map((h) => ({
    type: 'Feature',
    properties: {
      name: h.name,
      detail: h.risk
        ? `Modelled risk ${h.risk.mean_score.toFixed(2)}, higher than ${(h.risk.london_percentile * 100).toFixed(0)}% of London`
        : '',
    },
    geometry: { type: 'Point', coordinates: [h.lng, h.lat] },
  }))
}

// Adds the sources and layers when the current style does not have them
// (first load and after a theme change), then sets their data.
function drawLayers(map: MapLibreMap, dark: boolean, context: MapContext | null, data: ContextData | null): void {
  const c = dark ? COLORS.dark : COLORS.light
  if (!map.getSource('areas')) {
    const empty = collection([])
    map.addSource('areas', { type: 'geojson', data: empty })
    map.addSource('route-shortest', { type: 'geojson', data: empty })
    map.addSource('route-lower', { type: 'geojson', data: empty })
    map.addSource('hotels', { type: 'geojson', data: empty })
    map.addLayer({ id: 'areas-fill', type: 'fill', source: 'areas', paint: { 'fill-color': c.area, 'fill-opacity': 0.12 } })
    map.addLayer({
      id: 'areas-line',
      type: 'line',
      source: 'areas',
      paint: {
        'line-color': ['match', ['get', 'kind'], 'hotels', c.hotel, c.area],
        'line-width': 2,
        'line-dasharray': [2, 1.5],
      },
    })
    const round = { 'line-cap': 'round', 'line-join': 'round' } as const
    map.addLayer({
      id: 'route-shortest-line',
      type: 'line',
      source: 'route-shortest',
      layout: round,
      paint: { 'line-color': c.shortest, 'line-width': 2.5 },
    })
    map.addLayer({
      id: 'route-lower-casing',
      type: 'line',
      source: 'route-lower',
      layout: round,
      paint: { 'line-color': c.casing, 'line-width': 8 },
    })
    map.addLayer({
      id: 'route-lower-line',
      type: 'line',
      source: 'route-lower',
      layout: round,
      paint: { 'line-color': c.lower, 'line-width': 5 },
    })
    map.addLayer({
      id: 'hotels-points',
      type: 'circle',
      source: 'hotels',
      paint: { 'circle-radius': 6, 'circle-color': c.hotel, 'circle-stroke-color': c.casing, 'circle-stroke-width': 2 },
    })
  }
  const set = (id: string, features: Feature[]) => map.getSource<GeoJSONSource>(id)?.setData(collection(features))
  set('areas', areaFeatures(context))
  set('route-shortest', routeFeatures(data?.routes ?? [], 'fast'))
  set('route-lower', routeFeatures(data?.routes ?? [], 'safe'))
  set('hotels', hotelFeatures(data?.hotels ?? []))
}

function contextBounds(context: MapContext | null, data: ContextData | null): maplibregl.LngLatBounds | null {
  const bounds = new maplibregl.LngLatBounds()
  if (context) {
    for (const p of context.places) bounds.extend([p.lng, p.lat])
    for (const circle of [...context.areas, ...context.hotelSearches]) {
      for (const point of circleRing(circle, 8)) bounds.extend(point)
    }
    for (const r of context.routes) {
      bounds.extend([r.from.lng, r.from.lat])
      bounds.extend([r.to.lng, r.to.lat])
    }
  }
  for (const r of data?.routes ?? []) {
    for (const point of [...r.fast.geometry.coordinates, ...r.safe.geometry.coordinates]) bounds.extend(point)
  }
  for (const h of data?.hotels ?? []) bounds.extend([h.lng, h.lat])
  return bounds.isEmpty() ? null : bounds
}

// Padding around the fitted bounds, reduced for a small map such as the
// mobile panel.
function fitPadding(container: HTMLElement): number {
  return Math.round(Math.min(56, 0.14 * Math.min(container.clientWidth, container.clientHeight)))
}

function placeMarker(map: MapLibreMap, label: string, lng: number, lat: number): Marker {
  const el = document.createElement('div')
  el.className = 'place-marker'
  el.setAttribute('role', 'img')
  el.setAttribute('aria-label', label)
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 14, className: 'map-popup' })
  popup.setText(label)
  el.addEventListener('mouseenter', () => popup.setLngLat([lng, lat]).addTo(map))
  el.addEventListener('mouseleave', () => popup.remove())
  const marker = new maplibregl.Marker({ element: el }).setLngLat([lng, lat]).addTo(map)
  marker.once('remove', () => popup.remove())
  return marker
}

export function ContextMap({ context }: { context: MapContext | null }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef<Marker[]>([])
  const boundsRef = useRef<maplibregl.LngLatBounds | null>(null)
  const appliedStyleRef = useRef<boolean | null>(null)
  // Incremented on each `style.load`; layers can only be added after it.
  const [styleVersion, setStyleVersion] = useState(0)
  const dark = usePrefersDark()
  const { data, state } = useContextData(context)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const initialDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    appliedStyleRef.current = initialDark
    const map = new maplibregl.Map({
      container,
      style: initialDark ? STYLE_DARK : STYLE_LIGHT,
      center: LONDON_CENTER,
      zoom: 10,
      attributionControl: { compact: true },
    })
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    map.on('style.load', () => setStyleVersion((v) => v + 1))

    const hotelPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, className: 'map-popup' })
    map.on('mousemove', 'hotels-points', (e) => {
      const feature = e.features?.[0]
      if (!feature || feature.geometry.type !== 'Point') return
      const name = String(feature.properties.name ?? '')
      const detail = String(feature.properties.detail ?? '')
      hotelPopup
        .setLngLat(feature.geometry.coordinates as [number, number])
        .setText(detail ? `${name}. ${detail}` : name)
        .addTo(map)
    })
    map.on('mouseleave', 'hotels-points', () => hotelPopup.remove())

    // The panel changes size when the layout changes or the mobile panel
    // opens; the map then needs a resize and the same bounds again.
    const observer = new ResizeObserver(() => {
      map.resize()
      if (boundsRef.current && container.clientHeight > 0) {
        map.fitBounds(boundsRef.current, { padding: fitPadding(container), maxZoom: 15, duration: 0 })
      }
    })
    observer.observe(container)
    mapRef.current = map
    return () => {
      observer.disconnect()
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || appliedStyleRef.current === dark) return
    appliedStyleRef.current = dark
    map.setStyle(dark ? STYLE_DARK : STYLE_LIGHT)
  }, [dark])

  useEffect(() => {
    const map = mapRef.current
    if (!map || styleVersion === 0 || appliedStyleRef.current !== dark) return
    try {
      drawLayers(map, dark, context, data)
    } catch {
      // The style is being replaced; the next `style.load` draws again.
      return
    }
    for (const marker of markersRef.current) marker.remove()
    markersRef.current = (context?.places ?? []).map((p) => placeMarker(map, p.label, p.lng, p.lat))
  }, [styleVersion, dark, context, data])

  useEffect(() => {
    const map = mapRef.current
    const bounds = contextBounds(context, data)
    boundsRef.current = bounds
    if (!map || !bounds) return
    map.fitBounds(bounds, { padding: fitPadding(map.getContainer()), maxZoom: 15, duration: 600 })
  }, [context, data])

  const empty = !context || isEmptyContext(context)
  return (
    <div
      className="context-map"
      data-routes-drawn={data?.routes.length ?? 0}
      data-areas-drawn={context ? context.areas.length : 0}
      data-style-loaded={styleVersion > 0}
    >
      <div ref={containerRef} className="map-canvas" role="region" aria-label="Map of the places in the selected answer" />
      {empty && <p className="map-empty">Places, areas and routes from an answer are drawn here.</p>}
      {!empty && context && <MapLegend context={context} data={data} loading={state === 'loading'} />}
    </div>
  )
}
