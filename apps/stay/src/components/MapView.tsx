import { useEffect, useRef, useState } from 'react'
import 'maplibre-gl/dist/maplibre-gl.css'
import maplibregl, { type ExpressionSpecification, type GeoJSONSource, type LngLatBoundsLike, type MapLayerMouseEvent } from 'maplibre-gl'
import type { Feature, FeatureCollection as GeoFeatureCollection, Geometry, Polygon } from 'geojson'
import type { Hotel, LngLat, RouteResponse } from '../api'
import { formatScore, RISK_CLASSES, riskClass } from '../risk'
import type { SearchPlace } from '../urlState'

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron'
const LABEL_FONT = ['Noto Sans Bold']
const ROUTE_SHORTEST_COLOUR = '#5b6472'
const ROUTE_LOWER_RISK_COLOUR = '#0b62d6'

type FeatureCollection = GeoFeatureCollection<Geometry>

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

function point(coordinates: LngLat, properties: Record<string, string | number>): Feature {
  return { type: 'Feature', geometry: { type: 'Point', coordinates }, properties }
}

function line(coordinates: LngLat[]): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates }, properties: {} }],
  }
}

/** Polygon approximating a circle of `radius` metres around a point. */
function circlePolygon(place: SearchPlace, radius: number): FeatureCollection {
  const ring: LngLat[] = []
  const latRadius = radius / 111_320
  const lngRadius = latRadius / Math.cos((place.lat * Math.PI) / 180)
  for (let i = 0; i <= 64; i++) {
    const angle = (i / 64) * 2 * Math.PI
    ring.push([place.lng + lngRadius * Math.cos(angle), place.lat + latRadius * Math.sin(angle)])
  }
  return {
    type: 'FeatureCollection',
    features: [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {} }],
  }
}

function hotelFeatures(hotels: Hotel[]): FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: hotels.map((h) => {
      const cls = riskClass(h.risk?.mean_score)
      return point([h.lng, h.lat], {
        id: h.id,
        name: h.name,
        fill: cls.fill,
        text: cls.text,
        scoreValue: h.risk?.mean_score ?? 0,
        score: h.risk ? formatScore(h.risk.mean_score).replace(/^0/, '') : '–',
      })
    }),
  }
}

function setData(map: maplibregl.Map, source: string, data: FeatureCollection) {
  const target = map.getSource(source) as GeoJSONSource | undefined
  target?.setData(data)
}

/** Step expression over a cluster's mean score that yields the class fill or text colour. */
function stepByClass(key: 'fill' | 'text'): ExpressionSpecification {
  const mean: ExpressionSpecification = ['/', ['get', 'scoreSum'], ['get', 'point_count']]
  const stops = RISK_CLASSES.slice(0, -1).flatMap((c, i) => [c.below, RISK_CLASSES[i + 1][key]])
  return ['step', mean, RISK_CLASSES[0][key], ...stops] as ExpressionSpecification
}

function addLayers(map: maplibregl.Map) {
  const sources = ['radius', 'route-shortest', 'route-lower-risk', 'focus', 'station', 'center']
  for (const id of sources) map.addSource(id, { type: 'geojson', data: EMPTY })
  // Overlapping markers are merged into a count marker until the map is zoomed in.
  // The count marker is coloured by the mean score of the hotels it contains.
  map.addSource('hotels', {
    type: 'geojson',
    data: EMPTY,
    cluster: true,
    clusterRadius: 22,
    clusterMaxZoom: 14,
    clusterProperties: { scoreSum: ['+', ['get', 'scoreValue']] },
  })

  map.addLayer({ id: 'radius-fill', type: 'fill', source: 'radius', paint: { 'fill-color': '#0f5c63', 'fill-opacity': 0.04 } })
  map.addLayer({
    id: 'radius-line',
    type: 'line',
    source: 'radius',
    paint: { 'line-color': '#0f5c63', 'line-width': 1.5, 'line-dasharray': [3, 3], 'line-opacity': 0.7 },
  })
  // The shortest route is drawn dashed above the lower-risk route so both stay visible where they coincide.
  map.addLayer({
    id: 'route-lower-risk',
    type: 'line',
    source: 'route-lower-risk',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ROUTE_LOWER_RISK_COLOUR, 'line-width': 7, 'line-opacity': 0.75 },
  })
  map.addLayer({
    id: 'route-shortest',
    type: 'line',
    source: 'route-shortest',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': ROUTE_SHORTEST_COLOUR, 'line-width': 3, 'line-dasharray': [1, 1.6] },
  })
  map.addLayer({
    id: 'focus-ring',
    type: 'circle',
    source: 'focus',
    paint: {
      'circle-radius': ['case', ['==', ['get', 'state'], 'selected'], 21, 18],
      'circle-color': '#ffffff',
      'circle-stroke-color': '#111827',
      'circle-stroke-width': ['case', ['==', ['get', 'state'], 'selected'], 3, 2],
    },
  })
  map.addLayer({
    id: 'clusters',
    type: 'circle',
    source: 'hotels',
    filter: ['has', 'point_count'],
    paint: { 'circle-radius': 16, 'circle-color': stepByClass('fill'), 'circle-stroke-color': '#111827', 'circle-stroke-width': 2.5 },
  })
  map.addLayer({
    id: 'cluster-count',
    type: 'symbol',
    source: 'hotels',
    filter: ['has', 'point_count'],
    layout: { 'text-field': ['concat', '×', ['to-string', ['get', 'point_count']]], 'text-font': LABEL_FONT, 'text-size': 12, 'text-allow-overlap': true },
    paint: { 'text-color': stepByClass('text') },
  })
  map.addLayer({
    id: 'hotels-circle',
    type: 'circle',
    source: 'hotels',
    filter: ['!', ['has', 'point_count']],
    paint: { 'circle-radius': 14, 'circle-color': ['get', 'fill'], 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 },
  })
  map.addLayer({
    id: 'hotels-score',
    type: 'symbol',
    source: 'hotels',
    filter: ['!', ['has', 'point_count']],
    layout: { 'text-field': ['get', 'score'], 'text-font': LABEL_FONT, 'text-size': 11, 'text-allow-overlap': true },
    paint: { 'text-color': ['get', 'text'] },
  })
  map.addLayer({
    id: 'station-circle',
    type: 'circle',
    source: 'station',
    paint: { 'circle-radius': 7, 'circle-color': '#ffffff', 'circle-stroke-color': '#c2410c', 'circle-stroke-width': 3 },
  })
  map.addLayer({
    id: 'station-label',
    type: 'symbol',
    source: 'station',
    layout: { 'text-field': ['get', 'name'], 'text-font': LABEL_FONT, 'text-size': 12, 'text-offset': [0, 1.2], 'text-anchor': 'top' },
    paint: { 'text-color': '#9a3412', 'text-halo-color': '#ffffff', 'text-halo-width': 2 },
  })
  map.addLayer({
    id: 'center-circle',
    type: 'circle',
    source: 'center',
    paint: { 'circle-radius': 6, 'circle-color': '#0f5c63', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 },
  })
  map.addLayer({
    id: 'center-label',
    type: 'symbol',
    source: 'center',
    layout: { 'text-field': ['get', 'name'], 'text-font': LABEL_FONT, 'text-size': 12, 'text-offset': [0, -1.1], 'text-anchor': 'bottom' },
    paint: { 'text-color': '#0f5c63', 'text-halo-color': '#ffffff', 'text-halo-width': 2 },
  })
}

function boundsOf(coordinates: LngLat[]): LngLatBoundsLike {
  const bounds = new maplibregl.LngLatBounds(coordinates[0], coordinates[0])
  for (const c of coordinates) bounds.extend(c)
  return bounds
}

interface Props {
  place: SearchPlace
  radius: number
  hotels: Hotel[]
  hoveredId: string | null
  selected: Hotel | null
  route: RouteResponse | null
  /** Width in pixels covered by the detail drawer on the left side of the map. */
  coveredLeft: number
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
}

export function MapView({ place, radius, hotels, hoveredId, selected, route, coveredLeft, onHover, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const popupRef = useRef<maplibregl.Popup | null>(null)
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  // Incremented when the container goes from hidden (zero size) to visible, so the view is fitted again.
  const [shownCount, setShownCount] = useState(0)
  const handlers = useRef({ onHover, onSelect })
  useEffect(() => {
    handlers.current = { onHover, onSelect }
  })

  useEffect(() => {
    if (!container.current) return
    let map: maplibregl.Map
    try {
      map = new maplibregl.Map({
        container: container.current,
        style: STYLE_URL,
        center: [place.lng, place.lat],
        zoom: 13,
        attributionControl: { compact: true },
      })
    } catch {
      // WebGL is unavailable. The list remains usable without the map.
      setFailed(true)
      return
    }
    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    popupRef.current = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 18, className: 'map-popup' })

    map.on('load', () => {
      addLayers(map)
      setReady(true)
    })
    map.on('mousemove', 'hotels-circle', (e: MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id as string | undefined
      map.getCanvas().style.cursor = 'pointer'
      if (id) handlers.current.onHover(id)
    })
    map.on('mouseleave', 'hotels-circle', () => {
      map.getCanvas().style.cursor = ''
      handlers.current.onHover(null)
    })
    map.on('click', 'hotels-circle', (e: MapLayerMouseEvent) => {
      const id = e.features?.[0]?.properties?.id as string | undefined
      if (id) handlers.current.onSelect(id)
    })
    map.on('click', 'clusters', (e: MapLayerMouseEvent) => {
      const feature = e.features?.[0]
      if (!feature || feature.geometry.type !== 'Point') return
      const centre = feature.geometry.coordinates as LngLat
      map.easeTo({ center: centre, zoom: map.getZoom() + 1.5 })
    })
    map.on('mouseenter', 'clusters', () => (map.getCanvas().style.cursor = 'pointer'))
    map.on('mouseleave', 'clusters', () => (map.getCanvas().style.cursor = ''))

    let wasHidden = container.current.clientWidth === 0
    const resize = new ResizeObserver(() => {
      map.resize()
      const hidden = map.getContainer().clientWidth === 0
      if (wasHidden && !hidden) setShownCount((n) => n + 1)
      wasHidden = hidden
    })
    resize.observe(container.current)
    return () => {
      resize.disconnect()
      popupRef.current?.remove()
      map.remove()
      mapRef.current = null
      setReady(false)
    }
    // The map is created once; later place changes are applied by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Search centre and radius outline; the view is fitted to the searched circle.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const circle = circlePolygon(place, radius)
    setData(map, 'radius', circle)
    setData(map, 'center', { type: 'FeatureCollection', features: [point([place.lng, place.lat], { name: place.label })] })
    const ring = (circle.features[0].geometry as Polygon).coordinates[0] as LngLat[]
    map.fitBounds(boundsOf(ring), { padding: 24, duration: 600 })
  }, [ready, place, radius, shownCount])

  useEffect(() => {
    const map = mapRef.current
    if (map && ready) setData(map, 'hotels', hotelFeatures(hotels))
  }, [ready, hotels])

  // Hovered and selected hotels are drawn from a separate unclustered source, so the
  // outline is visible even when the hotel's own marker is merged into a count marker.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const features: Feature[] = []
    const hovered = hoveredId && hoveredId !== selected?.id ? hotels.find((h) => h.id === hoveredId) : undefined
    if (selected) features.push(point([selected.lng, selected.lat], { state: 'selected' }))
    if (hovered) features.push(point([hovered.lng, hovered.lat], { state: 'hovered' }))
    setData(map, 'focus', { type: 'FeatureCollection', features })

    const labelled = hovered ?? (hoveredId === selected?.id ? selected : undefined)
    const popup = popupRef.current
    if (popup && labelled) {
      const text = labelled.risk ? `${labelled.name} · modelled risk ${formatScore(labelled.risk.mean_score)}` : labelled.name
      popup.setLngLat([labelled.lng, labelled.lat]).setText(text).addTo(map)
    } else {
      popup?.remove()
    }
  }, [ready, hotels, hoveredId, selected])

  // Station marker and both walking routes for the selected hotel.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    const station = selected?.nearest_station
    setData(
      map,
      'station',
      station ? { type: 'FeatureCollection', features: [point([station.lng, station.lat], { name: station.name })] } : EMPTY,
    )
    setData(map, 'route-shortest', route ? line(route.fast.geometry.coordinates) : EMPTY)
    setData(map, 'route-lower-risk', route ? line(route.safe.geometry.coordinates) : EMPTY)
    if (!selected) return
    const coordinates: LngLat[] = [[selected.lng, selected.lat]]
    if (station) coordinates.push([station.lng, station.lat])
    if (route) coordinates.push(...route.fast.geometry.coordinates, ...route.safe.geometry.coordinates)
    const narrow = map.getContainer().clientWidth < coveredLeft + 240
    map.fitBounds(boundsOf(coordinates), {
      // The bottom padding keeps the hotel and routes clear of the legend.
      padding: { top: 70, bottom: narrow ? 70 : 170, right: 70, left: 70 + (narrow ? 0 : coveredLeft) },
      maxZoom: 16.5,
      duration: 600,
    })
  }, [ready, selected, route, coveredLeft, shownCount])

  return (
    <div className="map" role="region" aria-label="Map of hotels. The same hotels are in the results list.">
      <div ref={container} className="map__canvas" />
      {failed && <p className="map__failed">The map could not be started in this browser. The list still works.</p>}
      <MapLegend showRoutes={route !== null} />
    </div>
  )
}

function MapLegend({ showRoutes }: { showRoutes: boolean }) {
  return (
    <div className="legend">
      <p className="legend__title">Modelled risk within 300 m (0 to 1)</p>
      <ul className="legend__classes">
        {RISK_LEGEND.map((c) => (
          <li key={c.label}>
            <span className="legend__swatch" style={{ background: c.fill }} />
            {c.label}
          </li>
        ))}
      </ul>
      <p className="legend__hint">
        <span className="legend__swatch legend__swatch--group" /> ×N: N hotels at one spot, coloured by their mean
      </p>
      {showRoutes && (
        <ul className="legend__routes">
          <li>
            <span className="legend__line legend__line--lower" /> Lower-risk walk
          </li>
          <li>
            <span className="legend__line legend__line--shortest" /> Shortest walk
          </li>
        </ul>
      )}
    </div>
  )
}

const RISK_LEGEND = [
  { label: '< .10', fill: riskClass(0).fill },
  { label: '.10–.19', fill: riskClass(0.1).fill },
  { label: '.20–.29', fill: riskClass(0.2).fill },
  { label: '.30–.44', fill: riskClass(0.3).fill },
  { label: '≥ .45', fill: riskClass(0.45).fill },
]
