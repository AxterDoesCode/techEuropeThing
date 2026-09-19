import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { HeatmapLayer } from '@deck.gl/aggregation-layers'
import { GeoJsonLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import type { CrimeRow, EventFeature, LngLat, RouteEndpoint, RouteResult } from '../types'
import type { Theme } from '../theme'
import { CATEGORY_COLOR, scoreColor } from './colors'
import { EventDetail } from '../panels/EventDetail'

const STYLE_URL: Record<Theme, string> = {
  dark: 'https://tiles.openfreemap.org/styles/dark',
  light: 'https://tiles.openfreemap.org/styles/positron',
}
const BUILDING_COLOR: Record<Theme, string> = { dark: '#2a2f3a', light: '#d9dce3' }
const LONDON: [number, number] = [-0.1, 51.505]
// Globe when zoomed out, mercator from this zoom up. deck.gl's HeatmapLayer does
// not render under the globe projection, and deck.gl accepts only the plain
// 'globe' and 'mercator' projection types (not MapLibre's interpolated form).
const MERCATOR_FROM_ZOOM = 7
const projectionFor = (zoom: number) => (zoom < MERCATOR_FROM_ZOOM ? 'globe' : 'mercator')

// Same ramp as scoreColor: blue, teal, yellow, orange, red
const HEATMAP_COLORS: [number, number, number][] = [
  [70, 130, 200],
  [90, 190, 180],
  [240, 200, 80],
  [240, 120, 50],
  [210, 30, 40],
]

interface Props {
  events: EventFeature[]
  crimeRows: CrimeRow[]
  selectedId: string | null
  target: (LngLat & { zoom?: number; nonce: number }) | null
  onSelect: (id: string | null) => void
  onHover: (pos: LngLat | null) => void
  onMapClick: (pos: LngLat) => void
  theme: Theme
  // Routing (all optional): computed routes, endpoint markers, and whether the
  // next click sets a route endpoint instead of selecting an event
  route?: RouteResult | null
  routeEndpoints?: Partial<Record<RouteEndpoint, LngLat | null>>
  picking?: boolean
}

interface RoutePath {
  kind: 'fast' | 'safe'
  path: [number, number][]
}
interface RouteMarker extends LngLat {
  kind: RouteEndpoint
}
const ROUTE_COLOR: Record<RoutePath['kind'], [number, number, number, number]> = {
  fast: [132, 140, 154, 230],
  safe: [38, 166, 91, 255],
}
const ENDPOINT_COLOR: Record<RouteEndpoint, [number, number, number, number]> = {
  origin: [38, 166, 91, 255],
  destination: [32, 36, 46, 255],
}

export function RiskMap({
  events, crimeRows, selectedId, target, onSelect, onHover, onMapClick, theme, route, routeEndpoints, picking,
}: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const overlayRef = useRef<MapboxOverlay | null>(null)
  const [popupNode, setPopupNode] = useState<HTMLDivElement | null>(null)
  const [projection, setProjection] = useState(projectionFor(1.5))
  const handlers = useRef({ onSelect, onHover, onMapClick })
  handlers.current = { onSelect, onHover, onMapClick }
  const pickingRef = useRef(false)
  pickingRef.current = picking ?? false
  const themeRef = useRef(theme)

  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!,
      style: STYLE_URL[themeRef.current],
      center: [10, 35],
      zoom: 1.5,
      maxPitch: 75,
      attributionControl: { compact: true },
    })
    const overlay = new MapboxOverlay({ interleaved: true, layers: [] })
    map.addControl(overlay)
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right')
    map.on('style.load', () => {
      map.setProjection({ type: projectionFor(map.getZoom()) })
      add3dBuildings(map, themeRef.current)
    })
    map.on('zoom', () => {
      const wanted = projectionFor(map.getZoom())
      if (map.getProjection()?.type !== wanted) {
        map.setProjection({ type: wanted })
        setProjection(wanted)
      }
    })
    map.on('moveend', () => map.triggerRepaint())

    let frame = 0
    map.on('mousemove', (e) => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => handlers.current.onHover({ lng: e.lngLat.lng, lat: e.lngLat.lat }))
    })
    map.on('mouseout', () => {
      cancelAnimationFrame(frame)
      handlers.current.onHover(null)
    })
    map.once('load', () => {
      map.flyTo({ center: LONDON, zoom: 11.5, pitch: 50, bearing: -15, duration: 6000, essential: true })
    })
    mapRef.current = map
    overlayRef.current = overlay
    // Console access for debugging: open the app with ?debug
    if (location.search.includes('debug')) {
      Object.assign(window, { __map: map, __overlay: overlay })
    }
    return () => {
      cancelAnimationFrame(frame)
      map.remove()
      mapRef.current = null
      overlayRef.current = null
    }
  }, [])

  // setStyle fires 'style.load' again, which restores the projection and buildings
  useEffect(() => {
    if (themeRef.current === theme) return
    themeRef.current = theme
    mapRef.current?.setStyle(STYLE_URL[theme])
  }, [theme])

  useEffect(() => {
    if (target) {
      mapRef.current?.flyTo({ center: [target.lng, target.lat], zoom: target.zoom ?? mapRef.current.getZoom(), duration: 1500 })
    }
  }, [target])

  // Show the whole route when a new one arrives; the left sidebar covers 340 px
  useEffect(() => {
    const map = mapRef.current
    if (!map || !route) return
    const coords = [...route.fast.geometry.coordinates, ...route.safe.geometry.coordinates]
    const bounds = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]))
    const wide = map.getContainer().clientWidth > 800
    map.fitBounds(bounds, { padding: { top: 60, bottom: 60, right: 60, left: wide ? 400 : 60 }, duration: 1200, maxZoom: 16 })
  }, [route])

  const routeLayers = useMemo(() => {
    // fast first, so the safe path is drawn over it where they overlap
    const paths: RoutePath[] = route
      ? [
          { kind: 'fast', path: route.fast.geometry.coordinates },
          { kind: 'safe', path: route.safe.geometry.coordinates },
        ]
      : []
    const markers: RouteMarker[] = (['origin', 'destination'] as const).flatMap((kind) => {
      const p = routeEndpoints?.[kind]
      return p ? [{ kind, lng: p.lng, lat: p.lat }] : []
    })
    return [
      new PathLayer<RoutePath>({
        id: 'route-paths',
        data: paths,
        getPath: (d) => d.path,
        getColor: (d) => ROUTE_COLOR[d.kind],
        getWidth: (d) => (d.kind === 'safe' ? 5 : 7),
        widthUnits: 'pixels',
        capRounded: true,
        jointRounded: true,
        // Paths are at ground level; keep them visible in front of extruded buildings
        parameters: { depthCompare: 'always' },
      }),
      new ScatterplotLayer<RouteMarker>({
        id: 'route-endpoints',
        data: markers,
        getPosition: (d) => [d.lng, d.lat],
        getRadius: 8,
        radiusUnits: 'pixels',
        getFillColor: (d) => ENDPOINT_COLOR[d.kind],
        getLineColor: [255, 255, 255, 255],
        getLineWidth: 3,
        lineWidthUnits: 'pixels',
        stroked: true,
        parameters: { depthCompare: 'always' },
      }),
    ]
  }, [route, routeEndpoints])

  const selected = useMemo(() => events.find((e) => e.properties.id === selectedId) ?? null, [events, selectedId])

  // Popup anchored at the selected event. Content is rendered by React through a portal.
  const selectedKey = selected ? `${selected.properties.id}:${selected.properties.lng},${selected.properties.lat}` : null
  useEffect(() => {
    const map = mapRef.current
    if (!map || !selected) return
    const node = document.createElement('div')
// closeOnClick is off: the click that selects an event would also close its popup
    const popup = new maplibregl.Popup({ offset: 14, maxWidth: '360px', closeOnClick: false, className: 'event-popup' })
      .setLngLat([selected.properties.lng, selected.properties.lat])
      .setDOMContent(node)
      .addTo(map)
    // remove() also fires 'close'; only a close made by the user clears the selection
    let disposed = false
    popup.on('close', () => {
      if (!disposed) handlers.current.onSelect(null)
    })
    setPopupNode(node)
    return () => {
      disposed = true
      popup.remove()
      setPopupNode(null)
    }
    // Keyed on id and position so a data refresh does not reopen the popup
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedKey])

  const layers = useMemo(() => {
    const shapes = events.filter((e) => e.geometry.type !== 'Point')
    const points = events.filter((e) => e.geometry.type === 'Point')
    const isSelected = (f: EventFeature) => f.properties.id === selectedId
    return [
      // Metropolitan Police recorded crime, weighted by category, as a density surface
      new HeatmapLayer<CrimeRow>({
        // The id includes the projection so the layer is rebuilt on a switch; a
        // projection change alone does not make it aggregate again.
        id: `crime-heatmap-${projection}`,
        data: projection === 'mercator' ? crimeRows : [],
        getPosition: (r) => [r[0], r[1]],
        getWeight: (r) => r[3],
        radiusPixels: 40,
        intensity: 1,
        threshold: 0.04,
        colorRange: HEATMAP_COLORS,
        opacity: 0.65,
        aggregation: 'SUM',
      }),
      // Affected radius for events that only have a point location
      new ScatterplotLayer<EventFeature>({
        id: 'event-radius',
        data: points,
        getPosition: (f) => [f.properties.lng, f.properties.lat],
        getRadius: (f) => f.properties.radius_m,
        radiusUnits: 'meters',
        getFillColor: (f) => scoreColor(f.properties.risk, 50),
        getLineColor: (f) => scoreColor(f.properties.risk, 200),
        stroked: true,
        lineWidthMinPixels: 1,
      }),
      // Road segments (lines) and areas (polygons), coloured by current risk
      new GeoJsonLayer<EventFeature['properties']>({
        id: 'event-shapes',
        data: shapes,
        filled: true,
        stroked: true,
        getFillColor: (f) => scoreColor(f.properties.risk, 70),
        getLineColor: (f) => (isSelected(f as EventFeature) ? [255, 255, 255, 255] : scoreColor(f.properties.risk, 240)),
        getLineWidth: (f) => 3 + 6 * f.properties.severity,
        lineWidthUnits: 'pixels',
        lineCapRounded: true,
        lineJointRounded: true,
        pickable: true,
        updateTriggers: { getLineColor: selectedId },
      }),
      // Clickable marker for every event, tinted by category
      new ScatterplotLayer<EventFeature>({
        id: 'event-markers',
        data: events,
        getPosition: (f) => [f.properties.lng, f.properties.lat],
        getRadius: (f) => (isSelected(f) ? 11 : 7),
        radiusUnits: 'pixels',
        getFillColor: (f) => [...CATEGORY_COLOR[f.properties.category], 255],
        getLineColor: [255, 255, 255, 255],
        getLineWidth: (f) => (isSelected(f) ? 3 : 1.5),
        lineWidthUnits: 'pixels',
        stroked: true,
        pickable: true,
        // Markers stay visible in front of extruded buildings
        parameters: { depthCompare: 'always' },
        updateTriggers: { getRadius: selectedId, getLineWidth: selectedId },
      }),
    ]
  }, [events, crimeRows, selectedId, projection])

  useEffect(() => {
    overlayRef.current?.setProps({
      layers: [...layers, ...routeLayers],
      onClick: (info: PickingInfo) => {
        const feature = info.object as EventFeature | undefined
        if (pickingRef.current && info.coordinate) {
          handlers.current.onMapClick({ lng: info.coordinate[0], lat: info.coordinate[1] })
        } else if (feature?.properties?.id) handlers.current.onSelect(feature.properties.id)
        else if (info.coordinate) {
          handlers.current.onSelect(null)
          handlers.current.onMapClick({ lng: info.coordinate[0], lat: info.coordinate[1] })
        }
      },
      getTooltip: (info: PickingInfo) => (info.object as EventFeature | undefined)?.properties?.title ?? null,
      getCursor: ({ isHovering }: { isHovering: boolean }) => (isHovering ? 'pointer' : 'crosshair'),
    })
  }, [layers, routeLayers])

  return (
    <>
      <div ref={container} className="map" />
      {popupNode && selected && createPortal(<EventDetail event={selected} />, popupNode)}
    </>
  )
}

function add3dBuildings(map: maplibregl.Map, theme: Theme) {
  if (map.getLayer('buildings-3d') || !map.getSource('openmaptiles')) return
  map.addLayer({
    id: 'buildings-3d',
    type: 'fill-extrusion',
    source: 'openmaptiles',
    'source-layer': 'building',
    minzoom: 13,
    paint: {
      'fill-extrusion-color': BUILDING_COLOR[theme],
      'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 8],
      'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
      'fill-extrusion-opacity': 0.85,
    },
  })
}
