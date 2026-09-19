import type { RouteLeg, RouteResponse, Station } from '../api'
import type { RequestState } from '../hooks/useRequest'
import { formatDistance, formatMinutes, formatPercent, formatScore, straightLineMinutes } from '../risk'
import { ErrorNote, Loading } from './StatusBlocks'

function sameRoute(route: RouteResponse): boolean {
  return Math.abs(route.extra_distance_m) < 1 && Math.abs(route.fast.mean_risk - route.safe.mean_risk) < 0.0005
}

function showLit(route: RouteResponse): boolean {
  return route.fast.lit_share !== undefined || route.safe.lit_share !== undefined
}

function Comparison({ route }: { route: RouteResponse }) {
  if (sameRoute(route)) {
    return <p>The shortest route is also the lower-risk route here; the map shows one line.</p>
  }
  const extra = Math.round(route.extra_distance_m)
  const extraMinutes = route.safe.duration_min - route.fast.duration_min
  const reduction = Math.round(route.risk_reduction * 100)
  return (
    <p>
      The lower-risk route is {extra} m longer ({extraMinutes < 1 ? 'under 1 min' : `about ${formatMinutes(extraMinutes)}`}{' '}
      more) and its mean modelled risk along the way is {reduction}% lower than on the shortest route.
    </p>
  )
}

function RouteRow({ name, swatch, leg, showLit }: { name: string; swatch: string; leg: RouteLeg; showLit: boolean }) {
  return (
    <tr>
      <th scope="row">
        <span className={`legend__line ${swatch}`} aria-hidden="true" /> {name}
      </th>
      <td>{formatMinutes(leg.duration_min)}</td>
      <td>{formatDistance(leg.length_m)}</td>
      <td>{formatScore(leg.mean_risk)}</td>
      {showLit && <td>{leg.lit_share !== undefined ? formatPercent(leg.lit_share) : '–'}</td>}
    </tr>
  )
}

/** Turn-by-turn steps of one route. Rendered only when the service sends `steps`. */
function Steps({ name, leg }: { name: string; leg: RouteLeg }) {
  const steps = (leg.steps ?? []).filter((s) => s.distance_m > 0)
  if (steps.length === 0) return null
  return (
    <details className="steps">
      <summary>
        {name} route: {steps.length} {steps.length === 1 ? 'step' : 'steps'}
      </summary>
      <ol>
        {steps.map((step, i) => (
          <li key={i}>
            {step.instruction}
            <span className="muted">
              {' '}
              · {formatDistance(step.distance_m)} · {step.lit ? 'lit' : 'not recorded as lit'}
            </span>
          </li>
        ))}
      </ol>
    </details>
  )
}

function Unavailable({ status, detail, station }: { status: number; detail: string; station: Station }) {
  const fallback = (
    <p className="muted">
      Straight-line distance to {station.name}: {formatDistance(station.distance_m)}, about{' '}
      {formatMinutes(straightLineMinutes(station.distance_m))} on foot.
    </p>
  )
  if (status === 422) {
    return (
      <div className="status status--info" data-testid="route-unavailable">
        <p>
          A walking route cannot be calculated between this station and hotel. Both ends must be inside the area
          covered by the routing service and within 300 m of its walking network.
        </p>
        <p className="muted">Service message: {detail}</p>
        {fallback}
      </div>
    )
  }
  if (status === 503) {
    return (
      <div className="status status--info" data-testid="route-unavailable">
        <p>The routing service has no walking network loaded at the moment.</p>
        {fallback}
      </div>
    )
  }
  return null
}

interface Props {
  station: Station | null
  state: RequestState<RouteResponse>
  onRetry: () => void
  onShowMap?: () => void
}

export function RouteSection({ station, state, onRetry, onShowMap }: Props) {
  return (
    <section className="panel-section" aria-labelledby="route-heading">
      <h3 id="route-heading">{station ? `Walk from ${station.name}` : 'Walk from the nearest station'}</h3>
      {!station && <p className="muted">No station was found near this hotel, so no walk is shown.</p>}
      {station && state.status === 'loading' && <Loading>Calculating walking routes…</Loading>}
      {station && state.status === 'error' && (state.error.status === 422 || state.error.status === 503) && (
        <Unavailable status={state.error.status} detail={state.error.message} station={station} />
      )}
      {station && state.status === 'error' && state.error.status !== 422 && state.error.status !== 503 && (
        <ErrorNote onRetry={onRetry}>The walking routes could not be loaded. {state.error.message}</ErrorNote>
      )}
      {station && state.status === 'success' && (
        <div data-testid="route-loaded">
          <table className="routes">
            <thead>
              <tr>
                <th scope="col">Route</th>
                <th scope="col">Time</th>
                <th scope="col">Distance</th>
                <th scope="col">Mean modelled risk</th>
                {showLit(state.data) && <th scope="col">Lit streets</th>}
              </tr>
            </thead>
            <tbody>
              <RouteRow name="Shortest" swatch="legend__line--shortest" leg={state.data.fast} showLit={showLit(state.data)} />
              <RouteRow name="Lower-risk" swatch="legend__line--lower" leg={state.data.safe} showLit={showLit(state.data)} />
            </tbody>
          </table>
          <Comparison route={state.data} />
          <Steps name="Shortest" leg={state.data.fast} />
          {!sameRoute(state.data) && <Steps name="Lower-risk" leg={state.data.safe} />}
          {onShowMap && (
            <button type="button" className="button button--small" onClick={onShowMap}>
              Show both routes on the map
            </button>
          )}
        </div>
      )}
    </section>
  )
}
