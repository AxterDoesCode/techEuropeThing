import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection, LineString } from 'geojson'
import type { LngLat, RouteResponse } from '../api'
import { LONDON_CENTRE, bounds } from '../geo'
import type { EndpointKey, RouteKey } from '../types'

const STYLE_URL = {
  night: 'https://tiles.openfreemap.org/styles/dark',
  day: 'https://tiles.openfreemap.org/styles/positron',
}

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }
const NARROW = '(max-width: 720px)'

interface Props {
  origin: LngLat | null
  destination: LngLat | null
  route: RouteResponse | null
  selected: RouteKey
  night: boolean
  // Start of the step under the pointer in the step list.
  highlight: LngLat | null
  // Endpoint that the next map click sets; null when clicks are ignored.
  picking: EndpointKey | null
  // Changes when the map should fit the selected route again.
  fitToken: number
  onPick: (endpoint: EndpointKey, point: LngLat) => void
  onMove: (endpoint: EndpointKey, point: LngLat) => void
}

interface Scene {
  route: RouteResponse | null
  selected: RouteKey
  night: boolean
  highlight: LngLat | null
}

const line = (geometry: LineString): Feature => ({ type: 'Feature', geometry, properties: {} })

function setData(map: maplibregl.Map, id: string, data: Feature | FeatureCollection) {
  const source = map.getSource(id) as maplibregl.GeoJSONSource | undefined
  if (source) source.setData(data)
  else map.addSource(id, { type: 'geojson', data })
}

// Adds the route sources and layers when the style lacks them, then applies data and paint for the scene.
function applyScene(map: maplibregl.Map, scene: Scene) {
  setData(map, 'route-fast', scene.route ? line(scene.route.fast.geometry) : EMPTY)
  setData(map, 'route-safe', scene.route ? line(scene.route.safe.geometry) : EMPTY)
  setData(
    map,
    'step-highlight',
    scene.highlight ? { type: 'Feature', geometry: { type: 'Point', coordinates: scene.highlight }, properties: {} } : EMPTY,
  )

  const layout = { 'line-cap': 'round', 'line-join': 'round' } as const
  if (!map.getLayer('route-fast-line')) {
    map.addLayer({ id: 'route-fast-line', type: 'line', source: 'route-fast', layout, paint: {} })
    map.addLayer({ id: 'route-safe-casing', type: 'line', source: 'route-safe', layout, paint: {} })
    map.addLayer({ id: 'route-safe-line', type: 'line', source: 'route-safe', layout, paint: {} })
    map.addLayer({
      id: 'step-highlight-circle',
      type: 'circle',
      source: 'step-highlight',
      paint: { 'circle-radius': 8, 'circle-color': '#ffffff', 'circle-stroke-width': 4, 'circle-stroke-color': '#f59e0b' },
    })
  }

  const safeSelected = scene.selected === 'safe'
  const grey = scene.night ? '#aab2c0' : '#6b7280'
  map.setPaintProperty('route-fast-line', 'line-color', safeSelected ? grey : scene.night ? '#e5e7eb' : '#374151')
  map.setPaintProperty('route-fast-line', 'line-width', safeSelected ? 4 : 7)
  map.setPaintProperty('route-fast-line', 'line-opacity', safeSelected ? 0.85 : 1)
  map.setPaintProperty('route-safe-casing', 'line-color', scene.night ? '#042f2e' : '#ffffff')
  map.setPaintProperty('route-safe-casing', 'line-width', safeSelected ? 11 : 0)
  map.setPaintProperty('route-safe-line', 'line-color', scene.night ? '#2dd4bf' : '#0d9488')
  map.setPaintProperty('route-safe-line', 'line-width', safeSelected ? 7 : 4)
  map.setPaintProperty('route-safe-line', 'line-opacity', safeSelected ? 1 : 0.7)
  // The selected route is drawn above the other one.
  if (safeSelected) {
    map.moveLayer('route-fast-line', 'route-safe-casing')
  } else {
    map.moveLayer('route-fast-line', 'step-highlight-circle')
  }
}

// Padding that keeps the fitted route clear of the search card (left on wide screens, bottom on narrow ones).
function fitPadding(map: maplibregl.Map): maplibregl.PaddingOptions {
  const { clientWidth: w, clientHeight: h } = map.getContainer()
  if (window.matchMedia(NARROW).matches) {
    return { top: 60, left: 40, right: 40, bottom: Math.min(Math.round(h * 0.55) + 30, h - 160) }
  }
  return { top: 70, bottom: 70, right: 70, left: Math.min(460, Math.round(w * 0.5)) }
}

function markerElement(endpoint: EndpointKey): HTMLElement {
  const el = document.createElement('div')
  // MapLibre writes `transform` on the marker element, so the rotated pin shape is a child element.
  el.className = 'marker'
  const pin = document.createElement('span')
  pin.className = `marker-pin marker-${endpoint}`
  const letter = document.createElement('span')
  letter.textContent = endpoint === 'origin' ? 'A' : 'B'
  pin.append(letter)
  el.append(pin)
  el.setAttribute('role', 'img')
  el.setAttribute('aria-label', endpoint === 'origin' ? 'Start marker, draggable' : 'Destination marker, draggable')
  return el
}

export function MapView(props: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  // True between a 'style.load' event and the next setStyle call.
  const styleReadyRef = useRef(false)
  const markersRef = useRef<Record<EndpointKey, maplibregl.Marker | null>>({ origin: null, destination: null })
  const propsRef = useRef(props)
  propsRef.current = props
  const { origin, destination, route, selected, night, highlight, picking, fitToken } = props

  useEffect(() => {
    if (!containerRef.current) return
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: STYLE_URL[propsRef.current.night ? 'night' : 'day'],
      center: LONDON_CENTRE,
      zoom: 11.5,
      minZoom: 8,
      maxBounds: [[-1.2, 51.0], [1.0, 52.0]],
      attributionControl: false,
    })
    mapRef.current = map
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'top-right')
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    map.on('style.load', () => {
      styleReadyRef.current = true
      const p = propsRef.current
      applyScene(map, { route: p.route, selected: p.selected, night: p.night, highlight: p.highlight })
    })
    map.on('click', (e) => {
      const p = propsRef.current
      if (p.picking) p.onPick(p.picking, [e.lngLat.lng, e.lngLat.lat])
    })
    return () => {
      styleReadyRef.current = false
      markersRef.current = { origin: null, destination: null }
      mapRef.current = null
      map.remove()
    }
  }, [])

  // Basemap change. setStyle removes the route layers; the 'style.load' handler adds them again.
  const appliedNightRef = useRef(night)
  useEffect(() => {
    if (appliedNightRef.current === night) return
    appliedNightRef.current = night
    styleReadyRef.current = false
    mapRef.current?.setStyle(STYLE_URL[night ? 'night' : 'day'])
  }, [night])

  useEffect(() => {
    const map = mapRef.current
    if (map && styleReadyRef.current) applyScene(map, { route, selected, night, highlight })
  }, [route, selected, night, highlight])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const points: Record<EndpointKey, LngLat | null> = { origin, destination }
    for (const endpoint of ['origin', 'destination'] as const) {
      const point = points[endpoint]
      let marker = markersRef.current[endpoint]
      if (!point) {
        marker?.remove()
        markersRef.current[endpoint] = null
        continue
      }
      if (!marker) {
        marker = new maplibregl.Marker({ element: markerElement(endpoint), draggable: true, anchor: 'bottom' })
        const created = marker
        created.on('dragend', () => {
          const at = created.getLngLat()
          propsRef.current.onMove(endpoint, [at.lng, at.lat])
        })
        markersRef.current[endpoint] = marker
        marker.setLngLat(point).addTo(map)
      } else {
        marker.setLngLat(point)
      }
    }
  }, [origin, destination])

  // Fit to the selected route when a route arrives or a card is chosen.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !route) return
    const coords = route[selected].geometry.coordinates as LngLat[]
    if (coords.length === 0) return
    map.fitBounds(bounds(coords), { padding: fitPadding(map), maxZoom: 16.5, duration: 600 })
    // `route` and `selected` are read at the time the token changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitToken])

  // With one endpoint only, centre on it when it is outside the view.
  useEffect(() => {
    const map = mapRef.current
    const only = origin && !destination ? origin : destination && !origin ? destination : null
    if (!map || !only) return
    if (!map.getBounds().contains(only)) map.easeTo({ center: only, zoom: Math.max(map.getZoom(), 14) })
  }, [origin, destination])

  return (
    <div
      ref={containerRef}
      className={picking ? 'map picking' : 'map'}
      role="application"
      aria-label="Map of London. With a From or To field active, click the map to set that point."
    />
  )
}
