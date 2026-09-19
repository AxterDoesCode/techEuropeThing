import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_BASE, CRIME_REFRESH_MS, fetchAgents, fetchCrimePoints, POLL_INTERVAL_MS } from './api'
import { usePolling } from './usePolling'
import { useLiveEvents } from './useLiveEvents'
import { RiskMap, type MapTarget } from './map/RiskMap'
import { EventFeed } from './panels/EventFeed'
import { CoordinatePanel, type CoordinateFields } from './panels/CoordinatePanel'
import { AgentPanel } from './panels/AgentPanel'
import { LayerPanel } from './panels/LayerPanel'
import { RoutePanel } from './panels/RoutePanel'
import { SearchBox } from './panels/SearchBox'
import { useRouteState } from './route'
import { formatCoord } from './format'
import { useTheme } from './theme'
import { useLayerSettings, type LayerSettings } from './layerSettings'
import { isEventShown, listSources } from './eventFilter'
import type { PlaceResult } from './search'
import type { EventFeature, FlyTarget, LngLat } from './types'

export default function App() {
  const events = useLiveEvents()
  // usePolling reloads immediately when `load` changes identity, so a function
  // that depends on the agent_run counter reloads /api/agents on every such message
  const agentRuns = events.agentRuns
  const loadAgents = useCallback(() => {
    void agentRuns
    return fetchAgents()
  }, [agentRuns])
  const agents = usePolling(loadAgents, POLL_INTERVAL_MS)
  const crime = usePolling(fetchCrimePoints, CRIME_REFRESH_MS)
  const [settings, updateSettings] = useLayerSettings()

  const [selectedId, setSelectedId] = useState<string | null>(null)
  // Position of the crime summary popup. At most one popup is open: selecting an
  // event clears this, and a crime query clears the selection.
  const [crimeAt, setCrimeAt] = useState<LngLat | null>(null)
  const [target, setTarget] = useState<MapTarget | null>(null)
  const [hover, setHover] = useState<LngLat | null>(null)
  const [fields, setFields] = useState<CoordinateFields>({ lng: '', lat: '' })
  const [theme, toggleTheme] = useTheme()
  const routing = useRouteState()

  // Source and minimum risk filters apply to the map and the feed alike
  const allEvents = events.events
  const disabledSources = useMemo(() => new Set(settings.disabledSources), [settings.disabledSources])
  const features = useMemo(
    () => allEvents.filter((e) => isEventShown(e, disabledSources, settings.minRisk)),
    [allEvents, disabledSources, settings.minRisk],
  )
  const sources = useMemo(() => listSources(allEvents, disabledSources), [allEvents, disabledSources])
  const error = events.error ?? agents.error ?? crime.error

  const selectEvent = useCallback((id: string | null) => {
    setSelectedId(id)
    if (id !== null) setCrimeAt(null)
  }, [])
  const queryCrime = useCallback((pos: LngLat | null) => {
    setCrimeAt(pos)
    if (pos !== null) setSelectedId(null)
  }, [])
  const changeSettings = useCallback(
    (patch: Partial<LayerSettings>) => {
      updateSettings(patch)
      if (patch.showCrime === false) setCrimeAt(null)
    },
    [updateSettings],
  )
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setSelectedId(null)
      setCrimeAt(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Moves the map to a position (optional zoom) or fits an area given as
  // { bbox: [west, south, east, north] }. Used by the Position panel and the search box.
  const flyTo = useCallback((to: FlyTarget) => setTarget({ ...to, nonce: Date.now() }), [])
  // Areas and streets carry a bbox; stations, postcodes and coordinates are points
  const flyToPlace = useCallback(
    (place: PlaceResult) => flyTo(place.bbox ? { bbox: place.bbox } : { lng: place.lng, lat: place.lat, zoom: 15 }),
    [flyTo],
  )
  const resetView = useCallback(() => setTarget({ home: true, nonce: Date.now() }), [])

  const selectFromFeed = useCallback(
    (e: EventFeature) => {
      selectEvent(e.properties.id)
      flyTo({ lng: e.properties.lng, lat: e.properties.lat, zoom: 15 })
    },
    [selectEvent, flyTo],
  )
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
  const sidebarOpen = !settings.sidebarCollapsed
  const toggleSidebar = useCallback(
    () => changeSettings({ sidebarCollapsed: sidebarOpen }),
    [changeSettings, sidebarOpen],
  )
  // The B key toggles the sidebar, except while text is being entered, a modifier
  // key is held, or the list of the search box is open
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== 'b' || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return
      if (e.isComposing || e.repeat || e.defaultPrevented) return
      const el = e.target instanceof HTMLElement ? e.target : null
      if (el && (el.isContentEditable || el.closest('input, textarea, select'))) return
      if (document.querySelector('[role="combobox"][aria-expanded="true"]')) return
      toggleSidebar()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggleSidebar])

  return (
    <div className="app">
      <RiskMap
        events={features}
        crime={crime.data}
        showCrime={settings.showCrime}
        crimeOpacity={settings.crimeOpacity}
        crimeAt={crimeAt}
        onCrimeQuery={queryCrime}
        selectedId={selectedId}
        target={target}
        sidebarOpen={sidebarOpen}
        onSelect={selectEvent}
        onHover={setHover}
        onMapClick={onMapClick}
        theme={theme}
        route={routing.route}
        routeEndpoints={routeEndpoints}
        picking={routing.picking !== null}
      />
      {/* Kept mounted while collapsed so the panels keep their state */}
      <aside className="left" id="sidebar" hidden={!sidebarOpen}>
        <header className="panel header">
          <h1>London Live Risk Map</h1>
          {API_BASE && (
            <span className={`stream-status ${events.connected ? 'live' : ''}`}>{events.connected ? 'live' : 'polling'}</span>
          )}
          <button className="theme-toggle" onClick={toggleTheme} aria-label="Toggle light and dark mode">
            {theme === 'dark' ? 'Light' : 'Dark'}
          </button>
          <div className="view-controls">
            <button type="button" onClick={resetView}>Reset view</button>
          </div>
          {error && <p className="error">{error}</p>}
        </header>
        <SearchBox onSelect={flyToPlace} />
        <CoordinatePanel hover={hover} fields={fields} onChange={setFields} onGo={flyTo} />
        <LayerPanel
          crime={crime.data}
          settings={settings}
          onChange={changeSettings}
          sources={sources}
          totalEvents={allEvents.length}
          shownEvents={features.length}
        />
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
        <EventFeed
          events={features}
          total={allEvents.length}
          newIds={events.newIds}
          selectedId={selectedId}
          onSelect={selectFromFeed}
        />
        <AgentPanel agents={agents.data ?? []} />
      </aside>
      <button
        type="button"
        className={`sidebar-tab ${sidebarOpen ? '' : 'collapsed'}`}
        onClick={toggleSidebar}
        aria-label={sidebarOpen ? 'Hide sidebar' : 'Show sidebar'}
        aria-expanded={sidebarOpen}
        aria-controls="sidebar"
        title={sidebarOpen ? 'Hide sidebar (B)' : 'Show sidebar (B)'}
      >
        <span aria-hidden="true">{sidebarOpen ? '‹' : '›'}</span>
      </button>
      {!sidebarOpen && (
        <div className="panel view-controls collapsed-controls">
          <button type="button" onClick={resetView}>Reset view</button>
        </div>
      )}
    </div>
  )
}
