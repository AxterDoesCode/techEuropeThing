import type { EventFeature } from '../types'
import { CATEGORY_LABEL } from '../map/colors'
import { formatCoord } from '../format'

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString('en-GB', { timeZone: 'Europe/London' }) : '—')

export function EventDetail({ event }: { event: EventFeature }) {
  const p = event.properties
  return (
    <div className="detail">
      <h3>{p.title}</h3>
      <p className="meta">{CATEGORY_LABEL[p.category]}</p>
      {p.summary && <p className="summary">{p.summary}</p>}
      <dl>
        <dt>Risk now</dt><dd>{p.risk.toFixed(2)}</dd>
        <dt>Severity</dt><dd>{p.severity.toFixed(2)}</dd>
        <dt>Confidence</dt><dd>{p.confidence.toFixed(2)}</dd>
        <dt>Position</dt><dd>{formatCoord(p.lng)}, {formatCoord(p.lat)}</dd>
        <dt>Decay</dt><dd>{p.half_life_min ? `half-life ${p.half_life_min} min` : 'none while listed upstream'}</dd>
        <dt>Started</dt><dd>{fmt(p.occurred_at)}</dd>
        <dt>Expected end</dt><dd>{fmt(p.expires_at)}</dd>
        <dt>Sources</dt><dd>{p.source_ids.join(', ')}</dd>
      </dl>
      {p.urls.map((u) => (
        <a key={u} href={u} target="_blank" rel="noreferrer">{u}</a>
      ))}
    </div>
  )
}
