import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, postChat } from './api'
import { About } from './components/About'
import { Composer } from './components/Composer'
import { ContextMap } from './components/ContextMap'
import { EmptyState } from './components/EmptyState'
import { MessageList } from './components/MessageList'
import { estimateRetryAt, loadItems, newId, recordSend, saveItems, toMessages } from './conversation'
import type { Item } from './conversation'
import { buildMapContext, summariseContext } from './mapContext'

function lastAssistantId(items: Item[]): string | null {
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].kind === 'assistant') return items[i].id
  }
  return null
}

function errorItem(err: unknown): Item {
  if (err instanceof DOMException && err.name === 'AbortError') {
    return { id: newId(), kind: 'error', status: -1, detail: 'No answer was received for the last question.', retryAt: null }
  }
  if (err instanceof ApiError) {
    const retryAt = err.status === 429 ? estimateRetryAt(Date.now()) : null
    return { id: newId(), kind: 'error', status: err.status, detail: err.detail, retryAt }
  }
  return { id: newId(), kind: 'error', status: 0, detail: 'Unexpected error while requesting an answer.', retryAt: null }
}

// A page reload during a request leaves a user message without an answer. An
// error item is added so that the Retry button is offered for it.
function loadInitialItems(): Item[] {
  const items = loadItems()
  if (items.length > 0 && items[items.length - 1].kind === 'user') {
    items.push({ id: newId(), kind: 'error', status: -1, detail: 'No answer was received for the last question.', retryAt: null })
  }
  return items
}

export default function App() {
  const [items, setItems] = useState<Item[]>(loadInitialItems)
  const [selectedId, setSelectedId] = useState<string | null>(() => lastAssistantId(loadItems()))
  const [draft, setDraft] = useState('')
  const [focusToken, setFocusToken] = useState(0)
  const [waitingSince, setWaitingSince] = useState<number | null>(null)
  const [mapOpen, setMapOpen] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => saveItems(items), [items])
  useEffect(() => () => controllerRef.current?.abort(), [])

  const request = useCallback(async (history: Item[]) => {
    const controller = new AbortController()
    controllerRef.current = controller
    setItems(history)
    setWaitingSince(Date.now())
    recordSend(Date.now())
    let next: Item
    try {
      const response = await postChat(toMessages(history), controller.signal)
      next = { id: newId(), kind: 'assistant', response }
    } catch (err) {
      next = errorItem(err)
    }
    if (controllerRef.current !== controller) return
    controllerRef.current = null
    setItems([...history, next])
    if (next.kind === 'assistant') setSelectedId(next.id)
    setWaitingSince(null)
    setFocusToken((t) => t + 1)
  }, [])

  const waiting = waitingSince !== null
  // Error items are removed when a new request starts; the user message that
  // failed stays, so a retry sends the same message list again.
  const withoutErrors = useCallback(() => items.filter((item) => item.kind !== 'error'), [items])

  const send = useCallback(
    (text: string) => {
      const content = text.trim()
      if (content === '' || waiting) return
      setDraft('')
      void request([...withoutErrors(), { id: newId(), kind: 'user', content }])
    },
    [request, waiting, withoutErrors],
  )

  const retry = useCallback(() => {
    const history = withoutErrors()
    if (waiting || history.length === 0 || history[history.length - 1].kind !== 'user') return
    void request(history)
  }, [request, waiting, withoutErrors])

  const stop = useCallback(() => controllerRef.current?.abort(), [])

  const reset = useCallback(() => {
    controllerRef.current?.abort()
    controllerRef.current = null
    setItems([])
    setSelectedId(null)
    setWaitingSince(null)
    setDraft('')
    setFocusToken((t) => t + 1)
  }, [])

  const prefill = useCallback((text: string) => {
    setDraft(text)
    setFocusToken((t) => t + 1)
  }, [])

  const context = useMemo(() => {
    const selected = items.find((item) => item.id === selectedId)
    return selected && selected.kind === 'assistant' ? buildMapContext(selected.id, selected.response) : null
  }, [items, selectedId])

  return (
    <div className="app">
      <header className="app-header">
        <h1>Ask about London areas</h1>
        <div className="header-actions">
          <button type="button" className="header-button" onClick={reset} disabled={items.length === 0 && !waiting}>
            New conversation
          </button>
          <About />
        </div>
      </header>
      <main className="app-main">
        <section className="conversation" aria-label="Conversation">
          {items.length === 0 && !waiting ? (
            <EmptyState onAsk={send} disabled={waiting} />
          ) : (
            <MessageList
              items={items}
              selectedId={selectedId}
              waitingSince={waitingSince}
              onSelect={setSelectedId}
              onRetry={retry}
              onSend={send}
              onPrefill={prefill}
            />
          )}
        </section>
        <aside className={`map-panel${mapOpen ? ' is-open' : ''}`} aria-label="Context map">
          <button
            type="button"
            className="map-toggle"
            aria-expanded={mapOpen}
            aria-controls="map-body"
            onClick={() => setMapOpen((open) => !open)}
          >
            <span>{mapOpen ? 'Hide map' : 'Show map'}</span>
            <span className="map-toggle-summary">{summariseContext(context)}</span>
          </button>
          <div id="map-body" className="map-body">
            <ContextMap context={context} />
          </div>
        </aside>
        <div className="composer-area">
          <Composer
            value={draft}
            waiting={waiting}
            focusToken={focusToken}
            onChange={setDraft}
            onSubmit={() => send(draft)}
            onStop={stop}
          />
        </div>
      </main>
      <footer className="app-footer">
        <p>
          Answers use modelled risk from current events and police-recorded crime for the period stated in each answer (the crime data has no
          time of day).
          The assistant can be wrong; check the linked sources. Not an emergency service: call 999 in an emergency. Map
          data © OpenStreetMap contributors.
        </p>
      </footer>
    </div>
  )
}
