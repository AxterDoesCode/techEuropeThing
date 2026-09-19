import { metres, risk } from '../format'
import type { AlongState, NearbyEvent, SampledArea } from '../useAlongRoute'

const SOURCE_NAME: Record<string, string> = {
  tfl_road: 'TfL road disruptions',
  tfl_transit: 'TfL service status',
  bbc_london: 'BBC London',
  met_news: 'Metropolitan Police news',
  mylondon: 'MyLondon',
  standard_london: 'The Standard',
  ea_floods: 'Environment Agency flood warnings',
}

const sourceName = (id: string): string => SOURCE_NAME[id] ?? id.replace(/_/g, ' ')
const categoryName = (category: string): string => category.replace(/_/g, ' ')

function where(area: SampledArea): string {
  if (area.position === 'start') return 'near the start'
  if (area.position === 'end') return 'near the end'
  return `about ${metres(area.along_m)} along the route`
}

function Highest({ area }: { area: SampledArea }) {
  const percentile = Math.round(area.risk.london_percentile * 100)
  return (
    <p className="highest">
      Highest sampled area, {where(area)}: modelled risk <strong>{risk(area.risk.mean_score)}</strong>, higher than{' '}
      <strong>{percentile}%</strong> of London.
    </p>
  )
}

function EventItem({ event }: { event: NearbyEvent }) {
  return (
    <li className="event">
      <span className="event-title">{event.title}</span>
      <span className="event-meta">
        <span className="tag">{categoryName(event.category)}</span>
        {event.distance_m !== null && <span>{metres(event.distance_m)} from the route</span>}
        {event.source_ids.length > 0 && <span>{event.source_ids.map(sourceName).join(', ')}</span>}
        {event.url && <a href={event.url} target="_blank" rel="noreferrer noopener">Source<span className="sr-only"> for {event.title}</span></a>}
      </span>
    </li>
  )
}

export function AlongRoute({ state, onRetry }: { state: AlongState; onRetry: () => void }) {
  if (state.status === 'idle') return null
  return (
    <section aria-label="Along this route" aria-busy={state.status === 'loading'}>
      <h2>Along this route</h2>
      {state.status === 'loading' && <p className="fine">Checking current events near the route…</p>}
      {state.status === 'error' && (
        <p className="notice error" role="alert">
          {state.message} <button type="button" className="link-button" onClick={onRetry}>Retry</button>
        </p>
      )}
      {state.status === 'ready' && (
        <>
          {state.data.highest && <Highest area={state.data.highest} />}
          {state.data.events.length === 0 ? (
            <p className="fine">No current events within 250 m of the {state.data.sampled} sampled points.</p>
          ) : (
            <ul className="events">{state.data.events.map((event) => <EventItem key={event.id} event={event} />)}</ul>
          )}
          {state.data.failed > 0 && (
            <p className="notice">
              {state.data.failed} of {state.data.sampled} area requests failed; the list may be incomplete.{' '}
              <button type="button" className="link-button" onClick={onRetry}>Retry</button>
            </p>
          )}
        </>
      )}
    </section>
  )
}
