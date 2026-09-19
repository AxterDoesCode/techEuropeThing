import { useEffect, useMemo, useRef, useState } from 'react'
import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import type { Cell, EventFeature } from '../types'
import { CATEGORY_COLOR, scoreColor } from './colors'

const STYLE_URL = 'https://tiles.openfreemap.org/styles/dark'
const LONDON: [number, number] = [-0.1, 51.505]
// Below this zoom the coarse (res 7) cells are shown
const FINE_ZOOM = 11
const MAX_EXTRUSION_M = 1500

interface Props {
  cellsFine: Cell[]
  cellsCoarse: Cell[]
  events: EventFeature[]
  selectedId: string | null
  flyTo: { lng: number; lat: number; nonce: number } | null
  onSelect: (id: string | null) => void
}

export function RiskMap({ cellsFine, cellsCoarse, events, selectedId, flyTo, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const overlayRef = useRef<MapboxOverlay | null>(null)
  const [zoom, setZoom] = useState(1.5)

  useEffect(() => {
    const map = new maplibregl.Map({
      container: container.current!,
      style: STYLE_URL,
      center: [10, 35],
      zoom: 1.5,
      maxPitch: 75,
      attributionControl: { compact: true },
    })
    const overlay = new MapboxOverlay({ interleaved: true, layers: [] })
    map.addControl(overlay)
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-right')
    map.on('style.load', () => {
      map.setProjection({ type: 'globe' })
      add3dBuildings(map)
    })
    map.on('zoom', () => setZoom(map.getZoom()))
    map.on('moveend', () => map.triggerRepaint())
    map.once('load', () => {
      map.flyTo({ center: LONDON, zoom: 11.5, pitch: 50, bearing: -15, duration: 6000, essential: true })
    })
    mapRef.current = map
    // Console access for debugging: open the app with ?debug
    if (location.search.includes('debug')) (window as unknown as { __map: unknown }).__map = map
    overlayRef.current = overlay
    return () => {
      map.remove()
      mapRef.current = null
      overlayRef.current = null
    }
  }, [])

  useEffect(() => {
    if (flyTo) mapRef.current?.flyTo({ center: [flyTo.lng, flyTo.lat], zoom: 15, pitch: 55, duration: 2000 })
  }, [flyTo])

  const showFine = zoom >= FINE_ZOOM
  const layers = useMemo(() => {
    const areas = events.filter((e) => e.geometry.type !== 'Point')
    return [
      new H3HexagonLayer<Cell>({
        id: 'cells',
        data: showFine ? cellsFine : cellsCoarse,
        getHexagon: (c) => c.h3,
        getFillColor: (c) => scoreColor(c.score, 170),
        getElevation: (c) => c.score * MAX_EXTRUSION_M * (showFine ? 1 : 4),
        extruded: true,
        elevationScale: 1,
        coverage: 0.9,
        pickable: true,
        transitions: { getElevation: 600, getFillColor: 600 },
        updateTriggers: { getElevation: showFine },
      }),
      new GeoJsonLayer({
        id: 'event-areas',
        data: areas,
        filled: true,
        stroked: true,
        getFillColor: (f) => [...CATEGORY_COLOR[(f as EventFeature).properties.category], 60],
        getLineColor: (f) => [...CATEGORY_COLOR[(f as EventFeature).properties.category], 220],
        lineWidthMinPixels: 1.5,
        visible: showFine,
      }),
      new ScatterplotLayer<EventFeature>({
        id: 'events',
        data: events,
        getPosition: (f) => [f.properties.lng, f.properties.lat],
        getRadius: (f) => (f.properties.id === selectedId ? 10 : 4 + 6 * f.properties.risk),
        radiusUnits: 'pixels',
        getFillColor: (f) => [...CATEGORY_COLOR[f.properties.category], 230],
        getLineColor: [255, 255, 255, 255],
        getLineWidth: (f) => (f.properties.id === selectedId ? 2 : 0.5),
        lineWidthUnits: 'pixels',
        stroked: true,
        pickable: true,
        visible: showFine,
        updateTriggers: { getRadius: selectedId, getLineWidth: selectedId },
      }),
    ]
  }, [cellsFine, cellsCoarse, events, selectedId, showFine])

  useEffect(() => {
    overlayRef.current?.setProps({
      layers,
      onClick: (info: PickingInfo) => {
        if (info.layer?.id === 'events') onSelect((info.object as EventFeature).properties.id)
        else if (info.layer?.id === 'cells') onSelect((info.object as Cell).top_event_ids[0] ?? null)
        else onSelect(null)
      },
      getTooltip: (info: PickingInfo) => {
        if (info.layer?.id === 'events') return (info.object as EventFeature).properties.title
        if (info.layer?.id === 'cells') {
          const c = info.object as Cell
          return `Risk ${c.score.toFixed(2)} (live ${c.live.toFixed(2)}, baseline ${c.baseline.toFixed(2)})`
        }
        return null
      },
    })
  }, [layers, onSelect])

  return <div ref={container} className="map" />
}

function add3dBuildings(map: maplibregl.Map) {
  if (map.getLayer('buildings-3d') || !map.getSource('openmaptiles')) return
  map.addLayer({
    id: 'buildings-3d',
    type: 'fill-extrusion',
    source: 'openmaptiles',
    'source-layer': 'building',
    minzoom: 13,
    paint: {
      'fill-extrusion-color': '#2a2f3a',
      'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 8],
      'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
      'fill-extrusion-opacity': 0.85,
    },
  })
}
