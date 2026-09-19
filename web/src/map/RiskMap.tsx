import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { HeatmapLayer } from '@deck.gl/aggregation-layers'
import { GeoJsonLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import type { CrimePoints, CrimeRow, EventFeature, FlyTarget, LngLat, RouteEndpoint, RouteResult } from '../types'
import type { Theme } from '../theme'
import { CATEGORY_COLOR, scoreColor } from './colors'
import { buildCrimeIndex, CRIME_QUERY_RADIUS_M, summariseCrime } from './crimeIndex'
import { EventDetail } from '../panels/EventDetail'
import { CrimeDetail } from '../panels/CrimeDetail'
import { EventCard } from '../panels/EventCard'
import type { AssistantLayer } from '../assistant'
import type { AssistantEvent, ChatArea, MapContext } from '../chat'
import { bboxAround, bboxOf, clampFitBounds } from '../geo'
import { usePopup, usePopups, type PopupOptions } from './usePopups'

const STYLE_URL: Record<Theme, string> = {
  dark: 'https://tiles.openfreemap.org/styles/dark',
  light: 'https://tiles.openfreemap.org/styles/positron',
}
const BUILDING_COLOR: Record<Theme, string> = { dark: '#2a2f3a', light: '#d9dce3' }
const LONDON: [number, number] = [-0.1, 51.505]
// View after load and for "Reset view"
const HOME_VIEW = { center: LONDON, zoom: 11.5, pitch: 50, bearing: -15 }
// Width covered by the open sidebar, used as left padding when fitting bounds
const SIDEBAR_PADDING = 400
// Width covered by the open chat panel (12px + 360px + the toggle), used as right padding
const CHAT_PADDING = 400
// Globe when zoomed out, mercator from this zoom up. deck.gl's HeatmapLayer does
// not render under the globe projection, and deck.gl accepts only the plain
// 'globe' and 'mercator' projection types (not MapLibre's interpolated form).
const MERCATOR_FROM_ZOOM = 7
const projectionFor = (zoom: number) => (zoom < MERCATOR_FROM_ZOOM ? 'globe' : 'mercator')

// Same hues as scoreColor (blue, teal, yellow, orange, red). Alpha rises along the
// ramp so the low end of the domain is nearly transparent.
const HEATMAP_COLORS: [number, number, number, number][] = [
  [70, 130, 200, 30],
  [90, 190, 180, 120],
  [240, 200, 80, 200],
  [240, 120, 50, 225],
  [210, 30, 40, 235],
]
// The kernel radius is 300 m on the ground (Gaussian sigma = radius / 6 = 50 m),
// limited to this range of screen pixels: 300 m applies from zoom 12.3 to 14.7,
// the kernel is wider below (714 m at zoom 11) and narrower above (119 m at zoom 16).
const HEAT_RADIUS_M = 300
const HEAT_RADIUS_PX: [number, number] = [30, 160]
// Colour domain in weighted crimes per hectare per month for the 300 m kernel.
// At the first value the layer reaches the full alpha of the ramp (below it alpha
// falls linearly to 0); the second is the end of the ramp (red). The weights are
// 12-month MPS LSOA values for pedestrian-relevant offences, distributed on street
// points (backend/sources/mps_lsoa.py). In the 2025-09..2026-08 data the 300 m
// density at street points has median 0.39, 90th percentile 1.2, 99th percentile
// 3.8 and maximum 14 (Soho), so most of London is left undrawn.
const HEAT_DENSITY_DOMAIN: [number, number] = [1.5, 15]
// A wider kernel averages the peaks away (at 714 m: 99th percentile 1.9, maximum
// 7.5), so the domain is lowered with the kernel radius by these exponents, down
// to [0.75, 4.5] at 714 m and no further than HEAT_MAX_WIDENING. The colour of a place
// therefore depends on the zoom level, but not on the viewport or on how the map
// was navigated.
const HEAT_DOMAIN_EXPONENT: [number, number] = [0.8, 1.39]
const HEAT_MAX_WIDENING = 4
// Integral of the HeatmapLayer kernel exp(-u^2 / 0.05555) / 0.2954 over the unit disc
const HEAT_KERNEL_INTEGRAL = 0.5908
const EARTH_CIRCUMFERENCE_M = 40_075_016.686

function heatRadiusPixels(zoom: number): number {
  const metersPerPixel = (EARTH_CIRCUMFERENCE_M * Math.cos((LONDON[1] * Math.PI) / 180)) / (512 * 2 ** zoom)
  return Math.round(Math.min(HEAT_RADIUS_PX[1], Math.max(HEAT_RADIUS_PX[0], HEAT_RADIUS_M / metersPerPixel)))
}

// HeatmapLayer colours relative to the maximum of the current viewport, or, with
// colorDomain, by a value it multiplies with the texel size of its weight texture.
// That texel size depends on the zoom at which the texture bounds were last
// extended, so the same place changes colour with the navigation history. This
// subclass sets the domain in absolute units instead: a texel of the weight
// texture holds sum(weight * kernel(distance / radius)), which for a density rho
// (weight per m2) is rho * radius_m^2 * HEAT_KERNEL_INTEGRAL, independent of the
// texel size.
class DensityHeatmapLayer extends HeatmapLayer<CrimeRow> {
  static layerName = 'DensityHeatmapLayer'

  _updateWeightmap() {
    super._updateWeightmap()
    const { viewport } = this.context
    const metersPerPixel = viewport.distanceScales.metersPerUnit[2] / viewport.scale
    const radiusM = this.props.radiusPixels * metersPerPixel
    const perHectare = (HEAT_KERNEL_INTEGRAL * radiusM * radiusM) / 10_000
    const widening = Math.min(HEAT_MAX_WIDENING, Math.max(1, radiusM / HEAT_RADIUS_M))
    const bound = (i: 0 | 1) =>
      (HEAT_DENSITY_DOMAIN[i] / widening ** HEAT_DOMAIN_EXPONENT[i]) * perHectare * this.state.weightsScale
    this.state.colorDomain = [bound(0), bound(1)]
  }
}

export type MapTarget = (FlyTarget | { home: true }) & { nonce: number }

interface Props {
  events: EventFeature[]
  crime: CrimePoints | null
  showCrime: boolean
  /** heatmap opacity, 0 to 1 */
  crimeOpacity: number
  /** position of the open crime summary popup */
  crimeAt: LngLat | null
  /** a click that hit no event while the crime layer is visible; null = popup closed */
  onCrimeQuery: (pos: LngLat | null) => void
  selectedId: string | null
  target: MapTarget | null
  /** the sidebar covers the left part of the map */
  sidebarOpen: boolean
  onSelect: (id: string | null) => void
  onHover: (pos: LngLat | null) => void
  onMapClick: (pos: LngLat) => void
  theme: Theme
  // Routing (all optional): computed routes, endpoint markers, and whether the
  // next click sets a route endpoint instead of selecting an event
  route?: RouteResult | null
  routeEndpoints?: Partial<Record<RouteEndpoint, LngLat | null>>
  picking?: boolean
  /** what the chatbot drew: route, area, events and the ids shown as compact cards */
  assistant: AssistantLayer
  /** the chat panel covers the right part of the map */
  chatOpen: boolean
  onCloseCard: (eventId: string) => void
  /** set to a function that returns the current view; read by the chat when a question is sent */
  mapContextRef?: RefObject<(() => MapContext | null) | null>
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
type RGBA = [number, number, number, number]
// Everything the chatbot draws uses the accent colour of the theme (--accent in index.css)
const ASSISTANT_RGB: Record<Theme, [number, number, number]> = { dark: [240, 200, 80], light: [181, 122, 0] }
const ASSISTANT_FAST_COLOR: RGBA = [132, 140, 154, 150]

interface RouteStyle {
  color: Record<RoutePath['kind'], RGBA>
  width: Record<RoutePath['kind'], number>
  endpointColor: Record<RouteEndpoint, RGBA>
}
const MANUAL_ROUTE_STYLE: RouteStyle = { color: ROUTE_COLOR, width: { fast: 7, safe: 5 }, endpointColor: ENDPOINT_COLOR }

// Path and endpoint layers of one route source. The route of the Walking route
// panel and the route of the chatbot are separate sources with separate layer ids.
function buildRouteLayers(idPrefix: string, route: RouteResult | null, markers: RouteMarker[], style: RouteStyle) {
  // fast first, so the safe path is drawn over it where they overlap
  const paths: RoutePath[] = route
    ? [
        { kind: 'fast', path: route.fast.geometry.coordinates },
        { kind: 'safe', path: route.safe.geometry.coordinates },
      ]
    : []
  return {
    paths: new PathLayer<RoutePath>({
      id: `${idPrefix}-paths`,
      data: paths,
      getPath: (d) => d.path,
      getColor: (d) => style.color[d.kind],
      getWidth: (d) => style.width[d.kind],
      widthUnits: 'pixels',
      capRounded: true,
      jointRounded: true,
      // Paths are at ground level; keep them visible in front of extruded buildings
      parameters: { depthCompare: 'always' },
      updateTriggers: { getColor: style },
    }),
    endpoints: new ScatterplotLayer<RouteMarker>({
      id: `${idPrefix}-endpoints`,
      data: markers,
      getPosition: (d) => [d.lng, d.lat],
      getRadius: 8,
      radiusUnits: 'pixels',
      getFillColor: (d) => style.endpointColor[d.kind],
      getLineColor: [255, 255, 255, 255],
      getLineWidth: 3,
      lineWidthUnits: 'pixels',
      stroked: true,
      parameters: { depthCompare: 'always' },
      updateTriggers: { getFillColor: style },
    }),
  }
}

// Bounds the camera fits for an assistant layer: the route or the area, extended
// to the events shown as cards, limited to the London area
function assistantFitBounds(layer: AssistantLayer) {
  let target: [number, number][] | null = null
  if (layer.focus === 'route' && layer.route) {
    target = [...layer.route.fast.geometry.coordinates, ...layer.route.safe.geometry.coordinates]
  } else if (layer.focus === 'area' && layer.area) {
    const [west, south, east, north] = layer.area.bbox ?? bboxAround(layer.area.center, layer.area.radius_m)
    target = [[west, south], [east, north]]
  }
  if (!target) return null
  const cards = layer.events.filter((e) => layer.cardIds.includes(e.properties.id))
  const bbox = bboxOf([...target, ...cards.map((e): [number, number] => [e.properties.lng, e.properties.lat])])
  return bbox && clampFitBounds(bbox)
}

// Cards open while the user is typing in the chat panel: they do not take the keyboard focus
const CARD_POPUP: Omit<PopupOptions, 'onUserClose'> = {
  offset: 16, className: 'event-popup assistant-card', maxWidth: '220px', focusAfterOpen: false,
}
// Added to the fit padding of an assistant layer so that the cards of events at
// the ends of a route open inside the visible map area
const CARD_FIT_MARGIN = { top: 90, bottom: 0, left: 80, right: 80 }
// fitBounds computes the zoom for an unpitched camera. With pitch the near part of
// the view is magnified and the bounds extend past the padding; the fit of an
// assistant layer zooms out by this amount per degree of pitch (0.45 at the home pitch of 50).
const PITCH_ZOOM_MARGIN_PER_DEG = 0.009


export function RiskMap({
  events, crime, showCrime, crimeOpacity, crimeAt, onCrimeQuery, selectedId, target, sidebarOpen,
  onSelect, onHover, onMapClick, theme, route, routeEndpoints, picking, assistant, chatOpen, onCloseCard, mapContextRef,
}: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const overlayRef = useRef<MapboxOverlay | null>(null)
  const [projection, setProjection] = useState(projectionFor(1.5))
  const [heatRadius, setHeatRadius] = useState(HEAT_RADIUS_PX[0])
  const handlers = useRef({ onSelect, onHover, onMapClick, onCrimeQuery, onCloseCard })
  const crimeClickable = useRef(false)
  const panelsOpenRef = useRef({ sidebar: sidebarOpen, chat: chatOpen })
  const pickingRef = useRef(false)
  // Current values for the map and deck.gl callbacks, which are registered once.
  // Declared before the effects that read them.
  useEffect(() => {
    handlers.current = { onSelect, onHover, onMapClick, onCrimeQuery, onCloseCard }
    panelsOpenRef.current = { sidebar: sidebarOpen, chat: chatOpen }
    pickingRef.current = picking ?? false
  })
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
    // HeatmapLayer aggregates again 500 ms after the zoom stops and then uses this radius
    map.on('zoomend', () => setHeatRadius(heatRadiusPixels(map.getZoom())))

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
      map.flyTo({ ...HOME_VIEW, duration: 6000, essential: true })
    })
    mapRef.current = map
    overlayRef.current = overlay
    if (mapContextRef) {
      mapContextRef.current = () => {
        const round = (v: number) => Number(v.toFixed(5))
        const b = map.getBounds()
        const c = map.getCenter()
        return {
          center: [round(c.lng), round(c.lat)],
          bounds: [round(b.getWest()), round(b.getSouth()), round(b.getEast()), round(b.getNorth())],
          zoom: Number(map.getZoom().toFixed(2)),
        }
      }
    }
    // Console access for debugging: open the app with ?debug
    if (location.search.includes('debug')) {
      Object.assign(window, { __map: map, __overlay: overlay })
    }
    return () => {
      cancelAnimationFrame(frame)
      if (mapContextRef) mapContextRef.current = null
      map.remove()
      mapRef.current = null
      overlayRef.current = null
    }
  }, [mapContextRef])

  // setStyle fires 'style.load' again, which restores the projection and buildings
  useEffect(() => {
    if (themeRef.current === theme) return
    themeRef.current = theme
    mapRef.current?.setStyle(STYLE_URL[theme])
  }, [theme])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !target) return
    if ('home' in target) map.flyTo({ ...HOME_VIEW, duration: 1500 })
    else if ('bbox' in target) {
      const [west, south, east, north] = target.bbox
      map.fitBounds([[west, south], [east, north]], { padding: fitPadding(map, panelsOpenRef.current), duration: 1500, maxZoom: 16 })
    } else map.flyTo({ center: [target.lng, target.lat], zoom: target.zoom ?? map.getZoom(), duration: 1500 })
  }, [target])

  // Show the whole route when a new one arrives
  useEffect(() => {
    const map = mapRef.current
    if (!map || !route) return
    const coords = [...route.fast.geometry.coordinates, ...route.safe.geometry.coordinates]
    const bounds = coords.reduce((b, c) => b.extend(c), new maplibregl.LngLatBounds(coords[0], coords[0]))
    map.fitBounds(bounds, { padding: fitPadding(map, panelsOpenRef.current), duration: 1200, maxZoom: 16 })
  }, [route])

  // Show the route or the area of a new assistant layer. The nonce changes each time a layer is applied.
  const assistantRef = useRef(assistant)
  useEffect(() => {
    assistantRef.current = assistant
  })
  useEffect(() => {
    const map = mapRef.current
    const bounds = assistantFitBounds(assistantRef.current)
    if (!map || !bounds) return
    const [west, south, east, north] = bounds
    const base = fitPadding(map, panelsOpenRef.current)
    const padding = {
      top: base.top + CARD_FIT_MARGIN.top, bottom: base.bottom + CARD_FIT_MARGIN.bottom,
      left: base.left + CARD_FIT_MARGIN.left, right: base.right + CARD_FIT_MARGIN.right,
    }
    const camera = map.cameraForBounds([[west, south], [east, north]], { padding, maxZoom: 16 })
    if (!camera || camera.zoom === undefined) return
    map.flyTo({ ...camera, zoom: camera.zoom - PITCH_ZOOM_MARGIN_PER_DEG * map.getPitch(), duration: 1200 })
  }, [assistant.nonce])

  const routeLayers = useMemo(() => {
    const markers: RouteMarker[] = (['origin', 'destination'] as const).flatMap((kind) => {
      const p = routeEndpoints?.[kind]
      return p ? [{ kind, lng: p.lng, lat: p.lat }] : []
    })
    const { paths, endpoints } = buildRouteLayers('route', route ?? null, markers, MANUAL_ROUTE_STYLE)
    return [paths, endpoints]
  }, [route, routeEndpoints])

  // Drawn by the chatbot: the queried area, and a route whose endpoints are the ends of the safe path
  const assistantRoute = assistant.route
  const assistantArea = assistant.area
  const assistantGroundLayers = useMemo(() => {
    const rgb = ASSISTANT_RGB[theme]
    const ring = {
      data: assistantArea ? [assistantArea] : [],
      getPosition: (a: ChatArea) => a.center,
      getRadius: (a: ChatArea) => a.radius_m,
      radiusUnits: 'meters' as const,
      stroked: true,
      lineWidthUnits: 'pixels' as const,
      pickable: false,
      parameters: { depthCompare: 'always' as const },
    }
    const line = assistantRoute?.safe.geometry.coordinates ?? []
    const markers: RouteMarker[] = line.length
      ? [
          { kind: 'origin', lng: line[0][0], lat: line[0][1] },
          { kind: 'destination', lng: line[line.length - 1][0], lat: line[line.length - 1][1] },
        ]
      : []
    const { paths, endpoints } = buildRouteLayers('assistant-route', assistantRoute, markers, {
      color: { fast: ASSISTANT_FAST_COLOR, safe: [...rgb, 255] },
      width: { fast: 4, safe: 6 },
      endpointColor: { origin: [...rgb, 255], destination: ENDPOINT_COLOR.destination },
    })
    return [
      new ScatterplotLayer<ChatArea>({
        ...ring,
        id: 'assistant-area-outline',
        filled: false,
        getLineColor: [16, 19, 26, 200],
        getLineWidth: 5,
      }),
      new ScatterplotLayer<ChatArea>({
        ...ring,
        id: 'assistant-area',
        filled: true,
        getFillColor: [...rgb, 30],
        getLineColor: [...rgb, 255],
        getLineWidth: 2,
        updateTriggers: { getFillColor: theme, getLineColor: theme },
      }),
      paths,
      endpoints,
    ]
  }, [assistantRoute, assistantArea, theme])

  // An event of the chatbot can be absent from `events` (hidden by a filter, or not loaded)
  const assistantEvents = assistant.events
  const selected = useMemo(
    () => (selectedId === null ? null : (events.find((e) => e.properties.id === selectedId) ?? assistantEvents.find((e) => e.properties.id === selectedId) ?? null)),
    [events, assistantEvents, selectedId],
  )

  // Popup anchored at the selected event. Content is rendered by React through a portal.
  // Keyed on id and position so a data refresh does not reopen the popup.
  const selectedKey = selected ? `${selected.properties.id}:${selected.properties.lng},${selected.properties.lat}` : null
  const eventPopupOptions = useMemo<PopupOptions>(
    () => ({ ...DETAIL_POPUP, offset: 14, onUserClose: () => handlers.current.onSelect(null) }),
    [],
  )
  const eventPopupNode = usePopup(
    mapRef,
    selectedKey,
    selected ? { lng: selected.properties.lng, lat: selected.properties.lat } : null,
    eventPopupOptions,
  )

  // Compact cards of the chatbot's events, one popup per id in assistant.cardIds
  const cardEvents = useMemo(() => {
    const byId = new Map(assistantEvents.map((e) => [e.properties.id, e]))
    return assistant.cardIds.flatMap((id) => byId.get(id) ?? [])
  }, [assistantEvents, assistant.cardIds])
  const cardItems = useMemo(
    () => cardEvents.map((e) => ({ key: e.properties.id, at: { lng: e.properties.lng, lat: e.properties.lat } })),
    [cardEvents],
  )
  const cardPopupOptions = useMemo<PopupOptions>(
    () => ({ ...CARD_POPUP, onUserClose: (id) => handlers.current.onCloseCard(id) }),
    [],
  )
  const cardNodes = usePopups(mapRef, cardItems, cardPopupOptions)

  // Crime summary for a click that hit no event. HeatmapLayer is not pickable, so
  // the rows near the clicked position are looked up in a grid index.
  const crimeIndex = useMemo(() => (crime ? buildCrimeIndex(crime.rows) : null), [crime])
  const crimeSummary = useMemo(
    () => (crimeIndex && crimeAt ? summariseCrime(crimeIndex, crimeAt) : null),
    [crimeIndex, crimeAt],
  )
  const crimePopupOptions = useMemo<PopupOptions>(
    () => ({ ...DETAIL_POPUP, offset: crimePopupOffset, onUserClose: () => handlers.current.onCrimeQuery(null) }),
    [],
  )
  const crimePopupNode = usePopup(mapRef, crimeAt && showCrime ? `${crimeAt.lng},${crimeAt.lat}` : null, crimeAt, crimePopupOptions)
  // Area covered by the open crime summary: a circle of CRIME_QUERY_RADIUS_M on the
  // ground around the queried position, and a dot at the position. The white line
  // is drawn over a wider dark line so it is visible on the dark and the light
  // basemap and over the heatmap. Not pickable: clicks inside the circle reach the
  // map. No depth test: extruded buildings do not hide it.
  const crimeRingAt = showCrime ? crimeAt : null
  const crimeQueryLayers = useMemo(() => {
    const data = crimeRingAt ? [crimeRingAt] : []
    const ring = {
      data,
      getPosition: (p: LngLat): [number, number] => [p.lng, p.lat],
      getRadius: CRIME_QUERY_RADIUS_M,
      radiusUnits: 'meters' as const,
      stroked: true,
      lineWidthUnits: 'pixels' as const,
      pickable: false,
      parameters: { depthCompare: 'always' as const },
    }
    return [
      new ScatterplotLayer<LngLat>({
        ...ring,
        id: 'crime-query-ring-outline',
        filled: false,
        getLineColor: [16, 19, 26, 200],
        getLineWidth: 5,
      }),
      new ScatterplotLayer<LngLat>({
        ...ring,
        id: 'crime-query-ring',
        filled: true,
        getFillColor: [255, 255, 255, 26],
        getLineColor: [255, 255, 255, 255],
        getLineWidth: 2,
      }),
      new ScatterplotLayer<LngLat>({
        ...ring,
        id: 'crime-query-centre',
        getRadius: 3,
        radiusUnits: 'pixels',
        filled: true,
        getFillColor: [255, 255, 255, 255],
        getLineColor: [16, 19, 26, 230],
        getLineWidth: 1.5,
      }),
    ]
  }, [crimeRingAt])

  const crimeAvailable = showCrime && crimeIndex !== null && projection === 'mercator'
  useEffect(() => {
    crimeClickable.current = crimeAvailable
  }, [crimeAvailable])

  const layers = useMemo(() => {
    const shapes = events.filter((e) => e.geometry.type !== 'Point')
    const points = events.filter((e) => e.geometry.type === 'Point')
    const isSelected = (f: EventFeature) => f.properties.id === selectedId
    return [
      // Metropolitan Police recorded crime, weighted by category, as a density surface
      new DensityHeatmapLayer({
        // The id includes the projection so the layer is rebuilt on a switch; a
        // projection change alone does not make it aggregate again.
        id: `crime-heatmap-${projection}`,
        data: projection === 'mercator' && showCrime && crime ? crime.rows : [],
        getPosition: (r) => [r[0], r[1]],
        getWeight: (r) => r[3],
        radiusPixels: heatRadius,
        intensity: 1,
        colorRange: HEATMAP_COLORS,
        opacity: crimeOpacity,
        aggregation: 'SUM',
        parameters: { depthCompare: 'always' },
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
    ]
  }, [events, crime, showCrime, crimeOpacity, heatRadius, selectedId, projection])

  // Clickable marker for every event, tinted by category. A separate list because
  // the crime query circle is drawn between the layers above and the markers.
  const markerLayers = useMemo(() => {
    const isSelected = (f: EventFeature) => f.properties.id === selectedId
    return [
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
  }, [events, selectedId])

  // Events of the chatbot. The halos are drawn under the event markers and are not
  // pickable. The markers are drawn from the response itself, over the event
  // markers, so an event that the filters hide is still shown and clickable.
  const highlightIds = assistant.highlightIds
  const assistantHaloLayers = useMemo(() => {
    const rgb = ASSISTANT_RGB[theme]
    const highlighted = new Set(highlightIds)
    const isHighlighted = (f: AssistantEvent) => highlighted.has(f.properties.id)
    return [
      new ScatterplotLayer<AssistantEvent>({
        id: 'assistant-halos',
        data: assistantEvents,
        getPosition: (f) => [f.properties.lng, f.properties.lat],
        getRadius: (f) => (isHighlighted(f) ? 17 : 12),
        radiusUnits: 'pixels',
        getFillColor: (f) => [...rgb, isHighlighted(f) ? 90 : 35],
        getLineColor: (f) => [...rgb, isHighlighted(f) ? 255 : 150],
        getLineWidth: (f) => (isHighlighted(f) ? 2.5 : 1.5),
        lineWidthUnits: 'pixels',
        stroked: true,
        pickable: false,
        parameters: { depthCompare: 'always' },
        updateTriggers: { getFillColor: theme, getLineColor: theme },
      }),
    ]
  }, [assistantEvents, highlightIds, theme])
  const assistantMarkerLayers = useMemo(() => {
    const isSelected = (f: AssistantEvent) => f.properties.id === selectedId
    return [
      new ScatterplotLayer<AssistantEvent>({
        id: 'assistant-markers',
        data: assistantEvents,
        getPosition: (f) => [f.properties.lng, f.properties.lat],
        getRadius: (f) => (isSelected(f) ? 11 : 7),
        radiusUnits: 'pixels',
        getFillColor: (f) => [...CATEGORY_COLOR[f.properties.category], 255],
        getLineColor: [255, 255, 255, 255],
        getLineWidth: (f) => (isSelected(f) ? 3 : 1.5),
        lineWidthUnits: 'pixels',
        stroked: true,
        pickable: true,
        parameters: { depthCompare: 'always' },
        updateTriggers: { getRadius: selectedId, getLineWidth: selectedId },
      }),
    ]
  }, [assistantEvents, selectedId])

  useEffect(() => {
    overlayRef.current?.setProps({
      layers: [
        ...layers, ...crimeQueryLayers, ...assistantGroundLayers, ...assistantHaloLayers, ...markerLayers,
        ...assistantMarkerLayers, ...routeLayers,
      ],
      onClick: (info: PickingInfo) => {
        const feature = info.object as EventFeature | undefined
        if (pickingRef.current && info.coordinate) {
          handlers.current.onMapClick({ lng: info.coordinate[0], lat: info.coordinate[1] })
        } else if (feature?.properties?.id) handlers.current.onSelect(feature.properties.id)
        else if (info.coordinate) {
          const pos = { lng: info.coordinate[0], lat: info.coordinate[1] }
          handlers.current.onSelect(null)
          handlers.current.onCrimeQuery(crimeClickable.current ? pos : null)
          handlers.current.onMapClick(pos)
        }
      },
      getTooltip: (info: PickingInfo) => (info.object as EventFeature | undefined)?.properties?.title ?? null,
      getCursor: ({ isHovering }: { isHovering: boolean }) => (isHovering ? 'pointer' : 'crosshair'),
    })
  }, [layers, crimeQueryLayers, assistantGroundLayers, assistantHaloLayers, markerLayers, assistantMarkerLayers, routeLayers])

  return (
    <>
      <div ref={container} className="map" />
      {eventPopupNode && selected && createPortal(<EventDetail event={selected} />, eventPopupNode)}
      {crimePopupNode && crime && crimeAt && crimeSummary &&
        createPortal(<CrimeDetail month={crime.month} pos={crimeAt} summary={crimeSummary} />, crimePopupNode)}
      {cardEvents.map((e) => {
        const node = cardNodes.get(e.properties.id)
        return node && createPortal(
          <EventCard event={e} alongRoute={assistantRoute !== null} onOpen={(id) => handlers.current.onSelect(id)} />,
          node,
          e.properties.id,
        )
      })}
    </>
  )
}

// Both panels are open only on a window wide enough to leave a map area between them
function fitPadding(map: maplibregl.Map, open: { sidebar: boolean; chat: boolean }) {
  const wide = map.getContainer().clientWidth > 800
  return { top: 60, bottom: 60, right: wide && open.chat ? CHAT_PADDING : 60, left: wide && open.sidebar ? SIDEBAR_PADDING : 60 }
}

const DETAIL_POPUP = { className: 'event-popup', maxWidth: '360px' }

// The crime popup is placed outside the query circle so it does not cover it:
// the offset is the circle's radius on screen at the current zoom, plus a gap.
// 78271.517 m per pixel at zoom 0 on the equator with 512 px tiles.
function crimePopupOffset(map: maplibregl.Map, at: LngLat): number {
  const metersPerPixel = (78271.517 * Math.cos((at.lat * Math.PI) / 180)) / 2 ** map.getZoom()
  return Math.min(320, CRIME_QUERY_RADIUS_M / metersPerPixel) + 8
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
