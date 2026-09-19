import { useCallback, useMemo, useState } from 'react'
import { CRIME_REFRESH_MS, fetchAgents, fetchCrimePoints, fetchEvents, POLL_INTERVAL_MS } from './api'
import { usePolling } from './usePolling'
import { RiskMap } from './map/RiskMap'
import { EventFeed } from './panels/EventFeed'
import { CoordinatePanel, type CoordinateFields } from './panels/CoordinatePanel'
import { AgentPanel } from './panels/AgentPanel'
import { LayerPanel } from './panels/LayerPanel'
import { RoutePanel } from './panels/RoutePanel'
import { useRouteState } from './route'
import { formatCoord } from './format'
import { useTheme } from './theme'
import type { EventFeature, LngLat } from './types'

export default function App() {
  const events = usePolling(fetchEvents, POLL_INTERVAL_MS)
  const agents = usePolling(fetchAgents, POLL_INTERVAL_MS)
  const crime = usePolling(fetchCrimePoints, CRIME_REFRESH_MS)
  const [showCrime, setShowCrime] = useState(true)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [target, setTarget] = useState<(LngLat & { zoom?: number; nonce: number }) | null>(null)
  const [hover, setHover] = useState<LngLat | null>(null)
  const [fields, setFields] = useState<CoordinateFields>({ lng: '', lat: '' })
  const [theme, toggleTheme] = useTheme()
  const routing = useRouteState()

  const features = useMemo(() => events.data?.features ?? [], [events.data])
  const crimeRows = useMemo(() => (showCrime ? (crime.data?.rows ?? []) : []), [showCrime, crime.data])
  const error = events.error ?? agents.error ?? crime.error

  const selectFromFeed = useCallback((e: EventFeature) => {
    setSelectedId(e.properties.id)
    setTarget({ lng: e.properties.lng, lat: e.properties.lat, zoom: 15, nonce: Date.now() })
  }, [])
  const copyToFields = useCallback(
    (pos: LngLat) => setFields({ lng: formatCoord(pos.lng), lat: formatCoord(pos.lat) }),
    [],
  )
  // While a route endpoint is being picked, the click sets it instead of the Position fields
  const pickRoutePoint = routing.pick
  const onMapClick = useCallback(
    (pos: LngLat) => {
      if (!pickRoutePoint(pos)) copyToFields(pos)
    },
    [pickRoutePoint, copyToFields],
  )
  const routeEndpoints = useMemo(
    () => ({ origin: routing.origin, destination: routing.destination }),
    [routing.origin, routing.destination],
  )
  const goTo = useCallback((pos: LngLat) => setTarget({ ...pos, nonce: Date.now() }), [])

  return (
    <div className="app">
      <RiskMap
        events={features}
        crimeRows={crimeRows}
        selectedId={selectedId}
        target={target}
        onSelect={setSelectedId}
        onHover={setHover}
        onMapClick={onMapClick}
        theme={theme}
        route={routing.route}
        routeEndpoints={routeEndpoints}
        picking={routing.picking !== null}
      />
      <header className="panel header">
        <h1>London Live Risk Map</h1>
        <button className="theme-toggle" onClick={toggleTheme} aria-label="Toggle light and dark mode">
          {theme === 'dark' ? 'Light mode' : 'Dark mode'}
        </button>
        {error && <p className="error">{error}</p>}
      </header>
      <aside className="left">
        <CoordinatePanel hover={hover} fields={fields} onChange={setFields} onGo={goTo} />
        <LayerPanel crime={crime.data} showCrime={showCrime} onToggleCrime={setShowCrime} />
        <RoutePanel
          fields={routing.fields}
          origin={routing.origin}
          destination={routing.destination}
          picking={routing.picking}
          route={routing.route}
          onChange={routing.setFields}
          onPick={routing.setPicking}
          onRoute={routing.setRoute}
        />
        <EventFeed events={features} selectedId={selectedId} onSelect={selectFromFeed} />
        <AgentPanel agents={agents.data ?? []} />
      </aside>
    </div>
  )
}
