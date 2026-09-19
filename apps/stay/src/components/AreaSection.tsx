import type { AreaResponse, CrimeBlock, EventFeature } from '../api'
import type { RequestState } from '../hooks/useRequest'
import { crimePeriodLabel, formatDistance, humanise, sourceName } from '../risk'
import { ErrorNote, Loading } from './StatusBlocks'

const AREA_RADIUS_LABEL = '300 m'

function CountBars({ rows, label }: { rows: { name: string; count: number }[]; label: string }) {
  const max = Math.max(...rows.map((r) => r.count), 1)
  return (
    <table className="bars" aria-label={label}>
      <tbody>
        {rows.map((row) => (
          <tr key={row.name}>
            <th scope="row">{row.name}</th>
            <td className="bars__bar" aria-hidden="true">
              <span style={{ width: `${(row.count / max) * 100}%` }} />
            </td>
            <td className="bars__count">{row.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function CrimeSummary({ crime }: { crime: CrimeBlock }) {
  const categories = Object.entries(crime.top_categories)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([slug, count]) => ({ name: humanise(slug), count }))
  const streets = crime.top_streets.slice(0, 5).map((s) => ({ name: s.street, count: s.recorded_crimes }))
  return (
    <>
      <p className="stat">
        <span className="stat__number">{crime.recorded_crimes.toLocaleString('en-GB')}</span> recorded crimes within{' '}
        {AREA_RADIUS_LABEL}
        <span className="stat__period" data-testid="crime-period">
          Period: {crimePeriodLabel(crime)}
        </span>
      </p>
      <p className="note">
        Recorded crime is higher where many people gather (stations, nightlife, shopping streets). The police data has
        no time of day.
      </p>
      {categories.length > 0 && (
        <>
          <h4>Most frequent categories</h4>
          <CountBars rows={categories} label="Recorded crimes by category" />
        </>
      )}
      {streets.length > 0 && (
        <>
          <h4>Streets with the most records</h4>
          <CountBars rows={streets} label="Recorded crimes by street" />
        </>
      )}
      {crime.method && <p className="note">How the counts are derived: {crime.method}</p>}
    </>
  )
}

function safeHttpUrl(raw: string): string | null {
  try {
    const url = new URL(raw)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

function EventItem({ event }: { event: EventFeature }) {
  const p = event.properties
  const sources = p.source_ids ?? []
  const urls = (p.urls ?? []).map(safeHttpUrl).filter((u): u is string => u !== null)
  return (
    <li className="event">
      <p className="event__title">{p.title}</p>
      <p className="event__meta">
        {humanise(p.category)}
        {p.distance_m !== undefined && ` · ${formatDistance(p.distance_m)} away`}
        {p.occurred_at && ` · ${new Date(p.occurred_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}`}
      </p>
      {p.summary && <p className="event__summary">{p.summary}</p>}
      <p className="event__sources">
        Source:{' '}
        {sources.length === 0 && urls.length === 0 && 'not stated'}
        {sources.map((source, i) => {
          const url = urls[i]
          return (
            <span key={source}>
              {i > 0 && ', '}
              {url ? (
                <a className="link" href={url} target="_blank" rel="noopener noreferrer">
                  {sourceName(source)}
                </a>
              ) : (
                sourceName(source)
              )}
            </span>
          )
        })}
        {urls.slice(sources.length).map((url, i) => (
          <span key={url}>
            {(sources.length > 0 || i > 0) && ', '}
            <a className="link" href={url} target="_blank" rel="noopener noreferrer">
              {new URL(url).hostname}
            </a>
          </span>
        ))}
      </p>
    </li>
  )
}

interface Props {
  state: RequestState<AreaResponse>
  onRetry: () => void
}

export function AreaSection({ state, onRetry }: Props) {
  return (
    <section className="panel-section" aria-labelledby="area-heading">
      <h3 id="area-heading">Around this hotel</h3>
      {state.status === 'loading' && <Loading>Loading recorded crime and current events…</Loading>}
      {state.status === 'error' && (
        <ErrorNote onRetry={onRetry}>The area data could not be loaded. {state.error.message}</ErrorNote>
      )}
      {state.status === 'success' && (
        <div data-testid="area-loaded">
          {state.data.crime ? (
            <CrimeSummary crime={state.data.crime} />
          ) : (
            <p className="muted">No recorded crime data for this circle.</p>
          )}
          <h4>Current events</h4>
          {state.data.events.length === 0 ? (
            <p className="muted">No current events within {AREA_RADIUS_LABEL}.</p>
          ) : (
            <ul className="events">
              {state.data.events.map((event) => (
                <EventItem key={event.properties.id} event={event} />
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
