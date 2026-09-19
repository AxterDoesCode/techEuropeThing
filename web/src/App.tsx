import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ALERTS_REFRESH_MS, API_BASE, CRIME_REFRESH_MS, fetchAgents, fetchAlerts, fetchCrimePoints, POLL_INTERVAL_MS } from './api'
import { usePolling } from './usePolling'
import { useLiveEvents } from './useLiveEvents'
import { RiskMap, type MapTarget } from './map/RiskMap'
import { EventFeed } from './panels/EventFeed'
import { CoordinatePanel, type CoordinateFields } from './panels/CoordinatePanel'
import { AgentPanel } from './panels/AgentPanel'
import { LayerPanel } from './panels/LayerPanel'
import { RoutePanel } from './panels/RoutePanel'
import { SearchBox } from './panels/SearchBox'
import { AlertBanner } from './panels/AlertBanner'
import { ChatPanel } from './panels/ChatPanel'
import { useRouteState } from './route'
import { useAssistant } from './assistant'
import type { AssistantEvent, MapContext } from './chat'
import { useLetterShortcut } from './shortcuts'
import { formatCoord } from './format'
import { useTheme } from './theme'
import { isNarrowWindow, NARROW_WINDOW_PX, useLayerSettings, type LayerSettings } from './layerSettings'
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
  const alerts = usePolling(fetchAlerts, ALERTS_REFRESH_MS)
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
  // Map view for the chat request, read from the map when a question is sent
  const mapContextRef = useRef<(() => MapContext | null) | null>(null)
  const selectedIdRef = useRef(selectedId)
  useEffect(() => {
    selectedIdRef.current = selectedId
  })
  const getMapContext = useCallback((): MapContext | null => {
    const view = mapContextRef.current?.()
    if (!view) return null
    return selectedIdRef.current ? { ...view, selected_event_id: selectedIdRef.current } : view
  }, [])
  const assistant = useAssistant(getMapContext)

  // Source and minimum risk filters apply to the map and the feed alike
  const allEvents = events.events
  const disabledSources = useMemo(() => new Set(settings.disabledSources), [settings.disabledSources])
  const features = useMemo(
    () => allEvents.filter((e) => isEventShown(e, disabledSources, settings.minRisk)),
    [allEvents, disabledSources, settings.minRisk],
  )
  const sources = useMemo(() => listSources(allEvents, disabledSources), [allEvents, disabledSources])
  const error = events.error ?? agents.error ?? crime.error

  // The full event popup replaces the compact card of the same event
  const closeCard = assistant.closeCard
  const selectEvent = useCallback(
    (id: string | null) => {
      setSelectedId(id)
      if (id === null) return
      setCrimeAt(null)
      closeCard(id)
    },
    [closeCard],
  )
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
  // Also removes everything the chatbot drew; the conversation stays
  const clearAssistantLayer = assistant.clearLayer
  const resetView = useCallback(() => {
    clearAssistantLayer()
    setTarget({ home: true, nonce: Date.now() })
  }, [clearAssistantLayer])

  const selectFromFeed = useCallback(
    (e: EventFeature) => {
      selectEvent(e.properties.id)
      flyTo({ lng: e.properties.lng, lat: e.properties.lat, zoom: 15 })
    },
    [selectEvent, flyTo],
  )
  // An event listed under an older answer belongs to that answer's layer: draw it
  // again (without moving the camera to the whole layer) so the event is on the map
  const { showOnMap, layer: assistantLayer } = assistant
  const selectFromChat = useCallback(
    (entryId: number, e: AssistantEvent) => {
      if (assistantLayer.entryId !== entryId) showOnMap(entryId, false)
      selectFromFeed(e)
    },
    [assistantLayer.entryId, showOnMap, selectFromFeed],
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
  const chatOpen = !settings.chatCollapsed
  // On a narrow window only one of the two side panels is open: opening one collapses the other
  const toggleSidebar = useCallback(
    () => changeSettings(sidebarOpen ? { sidebarCollapsed: true } : { sidebarCollapsed: false, ...(isNarrowWindow() && { chatCollapsed: true }) }),
    [changeSettings, sidebarOpen],
  )
  const toggleChat = useCallback(
    () => changeSettings(chatOpen ? { chatCollapsed: true } : { chatCollapsed: false, ...(isNarrowWindow() && { sidebarCollapsed: true }) }),
    [changeSettings, chatOpen],
  )
  // A window resized below the limit with both panels open keeps the sidebar
  useEffect(() => {
    const narrow = window.matchMedia(`(max-width: ${NARROW_WINDOW_PX - 1}px)`)
    const onChange = () => {
      if (narrow.matches && sidebarOpen && chatOpen) changeSettings({ chatCollapsed: true })
    }
    narrow.addEventListener('change', onChange)
    return () => narrow.removeEventListener('change', onChange)
  }, [sidebarOpen, chatOpen, changeSettings])
  useLetterShortcut('b', toggleSidebar)
  useLetterShortcut('c', toggleChat)

  return (
    <div className={`app ${chatOpen ? 'chat-open' : ''}`}>
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
        assistant={assistant.layer}
        chatOpen={chatOpen}
        onCloseCard={assistant.closeCard}
        mapContextRef={mapContextRef}
      />
      <AlertBanner alerts={alerts.data} sidebarOpen={sidebarOpen} />
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
      {/* Kept mounted while collapsed so the conversation, the draft and a pending request are kept */}
      <aside className="right" id="chat-panel" hidden={!chatOpen}>
        <ChatPanel assistant={assistant} selectedId={selectedId} onSelectEvent={selectFromChat} />
      </aside>
      <button
        type="button"
        className={`sidebar-tab chat-tab ${chatOpen ? '' : 'collapsed'}`}
        onClick={toggleChat}
        aria-label={chatOpen ? 'Hide assistant' : 'Show assistant'}
        aria-expanded={chatOpen}
        aria-controls="chat-panel"
        title={chatOpen ? 'Hide assistant (C)' : 'Show assistant (C)'}
      >
        <span aria-hidden="true">{chatOpen ? '›' : '‹'}</span>
      </button>
      {!sidebarOpen && (
        <div className="panel view-controls collapsed-controls">
          <button type="button" onClick={resetView}>Reset view</button>
        </div>
      )}
    </div>
  )
}
