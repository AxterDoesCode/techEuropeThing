import { useCallback, useMemo, useState } from 'react'
import { fetchAgents, fetchCells, fetchEvents, POLL_INTERVAL_MS } from './api'
import { usePolling } from './usePolling'
import { RiskMap } from './map/RiskMap'
import { EventFeed } from './panels/EventFeed'
import { EventDetail } from './panels/EventDetail'
import { AgentPanel } from './panels/AgentPanel'
import type { EventFeature } from './types'

const loadFine = () => fetchCells(9)
const loadCoarse = () => fetchCells(7)

export default function App() {
  const fine = usePolling(loadFine, POLL_INTERVAL_MS)
  const coarse = usePolling(loadCoarse, POLL_INTERVAL_MS)
  const events = usePolling(fetchEvents, POLL_INTERVAL_MS)
  const agents = usePolling(fetchAgents, POLL_INTERVAL_MS)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [flyTo, setFlyTo] = useState<{ lng: number; lat: number; nonce: number } | null>(null)

  const features = useMemo(() => events.data?.features ?? [], [events.data])
  const selected = features.find((f) => f.properties.id === selectedId) ?? null
  const error = fine.error ?? coarse.error ?? events.error ?? agents.error

  const selectFromFeed = useCallback((e: EventFeature) => {
    setSelectedId(e.properties.id)
    setFlyTo({ lng: e.properties.lng, lat: e.properties.lat, nonce: Date.now() })
  }, [])

  return (
    <div className="app">
      <RiskMap
        cellsFine={fine.data ?? []}
        cellsCoarse={coarse.data ?? []}
        events={features}
        selectedId={selectedId}
        flyTo={flyTo}
        onSelect={setSelectedId}
      />
      <header className="panel header">
        <h1>London Live Risk Map</h1>
        {error && <p className="error">{error}</p>}
      </header>
      <aside className="left">
        <EventFeed events={features} selectedId={selectedId} onSelect={selectFromFeed} />
        <AgentPanel agents={agents.data ?? []} />
      </aside>
      {selected && <EventDetail event={selected} onClose={() => setSelectedId(null)} />}
    </div>
  )
}
