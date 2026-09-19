import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { Assistant, ChatEntry } from '../assistant'
import { CHAT_MODE, MAX_MESSAGE_CHARS, type AssistantEvent, type ChatUi } from '../chat'
import { CATEGORY_COLOR } from '../map/colors'
import './chat.css'

const EXAMPLES = [
  'Plan a walking route from Bloomsbury Square Garden to Euston station',
  'How safe is the Bethnal Green area?',
]

const km = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`)

interface Props {
  assistant: Assistant
  selectedId: string | null
  /** an event of the answer `entryId` was chosen in the list */
  onSelectEvent: (entryId: number, event: AssistantEvent) => void
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(since)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return <>{Math.max(0, Math.floor((now - since) / 1000))} s</>
}

function RouteSummary({ ui }: { ui: ChatUi }) {
  if (!ui.route) return null
  const { safe, fast } = ui.route
  return (
    <p className="meta">
      {ui.routeLabels?.origin && ui.routeLabels.destination && `${ui.routeLabels.origin} to ${ui.routeLabels.destination}. `}
      Lower-risk route {km(safe.length_m)}, {Math.round(safe.duration_min)} min; shortest route {km(fast.length_m)},{' '}
      {Math.round(fast.duration_min)} min.
    </p>
  )
}

// Relevant events of one answer: the highlighted ones first, each group in the
// order of the response (highest risk first)
function EventList({ entry, ui, selectedId, onSelectEvent }: { entry: ChatEntry; ui: ChatUi } & Pick<Props, 'selectedId' | 'onSelectEvent'>) {
  if (ui.events.length === 0) return null
  const highlighted = new Set(ui.highlightIds)
  const ordered = [
    ...ui.events.filter((e) => highlighted.has(e.properties.id)),
    ...ui.events.filter((e) => !highlighted.has(e.properties.id)),
  ]
  return (
    <>
      <h3>Relevant events ({ordered.length})</h3>
      <ul className="chat-events">
        {ordered.map((e) => {
          const p = e.properties
          const classes = `${highlighted.has(p.id) ? 'highlighted' : ''} ${p.id === selectedId ? 'selected' : ''}`
          return (
            <li key={p.id}>
              <button type="button" className={classes} onClick={() => onSelectEvent(entry.id, e)}>
                <span className="dot" style={{ background: `rgb(${CATEGORY_COLOR[p.category].join(',')})` }} />
                <span className="title">{p.title}</span>
                <span className="risk">{p.risk.toFixed(2)}</span>
              </button>
            </li>
          )
        })}
      </ul>
    </>
  )
}

export function ChatPanel({ assistant, selectedId, onSelectEvent }: Props) {
  const { entries, pendingSince, layer } = assistant
  const [draft, setDraft] = useState('')
  const list = useRef<HTMLDivElement>(null)
  const pending = pendingSince !== null

  // Keep the newest row in view
  useEffect(() => {
    const el = list.current
    if (el) el.scrollTop = el.scrollHeight
  }, [entries.length, pending])

  const submit = (text: string) => {
    if (pending || !text.trim()) return
    assistant.send(text)
    setDraft('')
  }
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return
    e.preventDefault()
    submit(draft)
  }

  return (
    <section className="panel chat">
      <header>
        <h2>Assistant{CHAT_MODE === 'demo' && ' (demo data)'}</h2>
        <button type="button" onClick={assistant.newChat} disabled={entries.length === 0 && !pending}>New chat</button>
      </header>
      <div className="chat-messages" ref={list} aria-live="polite">
        {entries.length === 0 && !pending && (
          <div className="chat-empty">
            <p className="meta">
              Ask for a walking route or about an area of London. Routes, areas and the events of an answer are drawn on
              the map; "Reset view" removes them.
            </p>
            {EXAMPLES.map((text) => (
              <button type="button" key={text} className="chip" onClick={() => submit(text)}>{text}</button>
            ))}
          </div>
        )}
        {entries.map((entry) => {
          const ui = entry.response?.ui
          return (
            <div key={entry.id} className={`chat-row ${entry.role} ${entry.failed ? 'failed' : ''}`} role={entry.role === 'error' ? 'alert' : undefined}>
              <p className="text">{entry.content}</p>
              {ui && (
                <>
                  <RouteSummary ui={ui} />
                  <EventList entry={entry} ui={ui} selectedId={selectedId} onSelectEvent={onSelectEvent} />
                  {layer.entryId !== entry.id && (
                    <button type="button" className="show-on-map" onClick={() => assistant.showOnMap(entry.id, true)}>Show on map</button>
                  )}
                </>
              )}
              {entry.response && entry.response.sources.length > 0 && (
                <ul className="chat-sources">
                  {entry.response.sources.map((s) => (
                    <li key={s.url}><a href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a></li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
        {pendingSince !== null && (
          <div className="chat-row working">
            <span>Working… <Elapsed since={pendingSince} /></span>
            <button type="button" onClick={assistant.cancel}>Cancel</button>
          </div>
        )}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit(draft)
        }}
      >
        <textarea
          value={draft} rows={2} maxLength={MAX_MESSAGE_CHARS} placeholder="Ask about a route or an area"
          aria-label="Question for the assistant" onChange={(e) => setDraft(e.target.value)} onKeyDown={onKeyDown}
        />
        <button type="submit" className="send" disabled={pending || !draft.trim()}>Send</button>
      </form>
    </section>
  )
}
