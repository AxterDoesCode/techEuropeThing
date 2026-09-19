import type { EventFeature } from '../types'
import { CATEGORY_COLOR, CATEGORY_LABEL } from '../map/colors'

interface Props {
  events: EventFeature[]
  selectedId: string | null
  onSelect: (e: EventFeature) => void
}

export function EventFeed({ events, selectedId, onSelect }: Props) {
  const sorted = [...events].sort((a, b) => b.properties.risk - a.properties.risk)
  return (
    <section className="panel feed">
      <h2>Active events ({events.length})</h2>
      <ul>
        {sorted.map((e) => {
          const p = e.properties
          return (
            <li key={p.id} className={p.id === selectedId ? 'selected' : ''} onClick={() => onSelect(e)}>
              <span className="dot" style={{ background: `rgb(${CATEGORY_COLOR[p.category].join(',')})` }} />
              <div>
                <div className="title">{p.title}</div>
                <div className="meta">
                  {CATEGORY_LABEL[p.category]} · risk {p.risk.toFixed(2)} · {p.source_ids.join(', ')}
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
