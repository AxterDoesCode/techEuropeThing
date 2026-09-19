import type { AssistantEvent } from '../chat'
import { CATEGORY_COLOR, CATEGORY_LABEL } from '../map/colors'

interface Props {
  event: AssistantEvent
  /** the events belong to a route: the distance is reported as distance from the route */
  alongRoute: boolean
  onOpen: (id: string) => void
}

// Compact card of an event the chatbot refers to, shown in a map popup. Opening it
// selects the event, which shows the full event popup.
export function EventCard({ event, alongRoute, onOpen }: Props) {
  const p = event.properties
  return (
    <button type="button" className="event-card" data-event-id={p.id} onClick={() => onOpen(p.id)} title="Open the event details">
      <span className="title">{p.title}</span>
      <span className="meta">
        <span className="dot" style={{ background: `rgb(${CATEGORY_COLOR[p.category].join(',')})` }} />
        {CATEGORY_LABEL[p.category]}{p.subtype ? ` · ${p.subtype}` : ''} · {p.ended_at === null ? 'ongoing' : 'ended'}
      </span>
      <span className="meta">
        risk {p.risk.toFixed(2)}
        {alongRoute && p.relevance && ` · ${Math.round(p.relevance.distance_m)} m from the route`}
      </span>
    </button>
  )
}
