import { useEffect, useState } from 'react'
import { API_BASE, fetchEvents, POLL_INTERVAL_MS } from './api'
import type { EventFeature } from './types'

// How long an event that arrived after the initial load is reported in `newIds`
const NEW_EVENT_MS = 60_000
// Full reload while the stream is open: `risk` decays with time without the row
// changing, so the stream alone does not refresh it
const RESYNC_MS = 5 * 60_000
// EventSource does not retry after an HTTP error response; the hook reopens it
const REOPEN_MS = 5_000
// The server closes every stream after 10 minutes and EventSource reconnects
// within a few seconds, resuming from Last-Event-ID. An interruption shorter than
// this is not shown as "polling" and does not cause a full reload.
const SHORT_GAP_MS = 10_000

export interface LiveEvents {
  events: EventFeature[]
  /** ids that arrived after the initial load, each for NEW_EVENT_MS */
  newIds: ReadonlySet<string>
  /** incremented on every `agent_run` message; use it to reload /api/agents */
  agentRuns: number
  /** true while the SSE stream is open; false = polling (or static sample mode) */
  connected: boolean
  error: string | null
}

const NO_IDS: ReadonlySet<string> = new Set()

// The stream sends `event_end` for ended events, so full loads drop them too and
// both modes show the same set.
const isShown = (f: EventFeature) => f.properties.ended_at === null

export function useLiveEvents(): LiveEvents {
  const [events, setEvents] = useState<EventFeature[]>([])
  const [newIds, setNewIds] = useState<ReadonlySet<string>>(NO_IDS)
  const [agentRuns, setAgentRuns] = useState(0)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let loaded = false
    const known = new Set<string>()
    const timers = new Set<ReturnType<typeof setTimeout>>()

    const later = (fn: () => void, ms: number) => {
      const t = setTimeout(() => {
        timers.delete(t)
        if (!cancelled) fn()
      }, ms)
      timers.add(t)
    }

    const markNew = (ids: string[]) => {
      if (ids.length === 0) return
      setNewIds((prev) => new Set([...prev, ...ids]))
      later(() => setNewIds((prev) => new Set([...prev].filter((id) => !ids.includes(id)))), NEW_EVENT_MS)
    }

    const load = () =>
      fetchEvents()
        .then((collection) => {
          if (cancelled) return
          const features = collection.features.filter(isShown)
          if (loaded) markNew(features.map((f) => f.properties.id).filter((id) => !known.has(id)))
          loaded = true
          known.clear()
          for (const f of features) known.add(f.properties.id)
          setEvents(features)
          setError(null)
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof Error ? e.message : String(e))
        })

    void load()
    if (!API_BASE) {
      return () => {
        cancelled = true
      }
    }

    let source: EventSource | null = null
    let mark: string | null = null
    let isOpen = false
    let downSince: number | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null
    const resyncTimer = setInterval(() => {
      if (isOpen) void load()
    }, RESYNC_MS)

    const setPolling = (on: boolean) => {
      if (on && pollTimer === null) pollTimer = setInterval(load, POLL_INTERVAL_MS)
      if (!on && pollTimer !== null) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    }

    const parse = <T,>(e: Event): T => {
      const msg = e as MessageEvent<string>
      if (msg.lastEventId) mark = msg.lastEventId
      return JSON.parse(msg.data) as T
    }

    const open = () => {
      const query = mark ? `?since=${encodeURIComponent(mark)}` : ''
      const es = new EventSource(`${API_BASE}/api/stream${query}`)
      source = es

      es.addEventListener('hello', (e) => {
        parse<unknown>(e)
        // the server replays a limited period only, so reload after a long interruption
        if (downSince !== null && Date.now() - downSince > SHORT_GAP_MS) void load()
        downSince = null
        isOpen = true
        setConnected(true)
        setPolling(false)
      })
      es.addEventListener('event_upsert', (e) => {
        const feature = parse<EventFeature>(e)
        const id = feature.properties.id
        if (!known.has(id)) {
          known.add(id)
          if (loaded) markNew([id])
        }
        setEvents((prev) => [...prev.filter((f) => f.properties.id !== id), feature])
      })
      es.addEventListener('event_end', (e) => {
        const { id } = parse<{ id: string }>(e)
        known.delete(id)
        setEvents((prev) => prev.filter((f) => f.properties.id !== id))
      })
      es.addEventListener('agent_run', (e) => {
        parse<unknown>(e)
        setAgentRuns((n) => n + 1)
      })
      es.addEventListener('cells_changed', (e) => {
        parse<unknown>(e) // cell scores are not drawn; only the mark is kept
      })
      es.onerror = () => {
        // Current data is kept. In state CONNECTING the browser retries by itself
        // and sends Last-Event-ID; in state CLOSED it does not.
        isOpen = false
        downSince ??= Date.now()
        later(() => {
          if (isOpen) return
          setConnected(false)
          setPolling(true)
        }, SHORT_GAP_MS)
        if (es.readyState === EventSource.CLOSED && source === es) {
          source = null
          later(open, REOPEN_MS)
        }
      }
    }
    open()

    return () => {
      cancelled = true
      source?.close()
      setPolling(false)
      clearInterval(resyncTimer)
      for (const t of timers) clearTimeout(t)
    }
  }, [])

  return { events, newIds, agentRuns, connected, error }
}
