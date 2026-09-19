import type { RouteLeg, RouteResponse } from '../api'
import { km, minutes, percent, risk, signedMetres, signedMinutes } from '../format'
import type { RouteKey } from '../types'

interface Props {
  route: RouteResponse
  selected: RouteKey
  onSelect: (key: RouteKey) => void
}

const TITLE: Record<RouteKey, string> = { safe: 'Lower risk', fast: 'Shortest' }

// True when the risk-weighted search returned the shortest path.
export function sameRoute(route: RouteResponse): boolean {
  return Math.abs(route.extra_distance_m) < 1 && Math.abs(route.safe.mean_risk - route.fast.mean_risk) < 0.0005
}

function comparison(route: RouteResponse): string {
  if (sameRoute(route)) return 'No lower-risk alternative for this trip'
  const parts = [
    signedMinutes(route.safe.duration_min - route.fast.duration_min),
    signedMetres(route.extra_distance_m),
  ]
  const change = Math.round(route.risk_reduction * 100)
  parts.push(change >= 0 ? `${change}% lower modelled risk` : `${-change}% higher modelled risk`)
  return `Lower-risk route: ${parts.join(', ')}`
}

function Card({ id, leg, selected, onSelect }: { id: RouteKey; leg: RouteLeg; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      className={`route-card ${id}${selected ? ' selected' : ''}`}
      data-route={id}
      onClick={onSelect}
    >
      <span className="route-card-title"><span className={`swatch ${id}`} aria-hidden="true" />{TITLE[id]}</span>
      <span className="route-card-time">{minutes(leg.duration_min)}</span>
      <span className="route-card-distance">{km(leg.length_m)}</span>
      <dl className="route-card-risk">
        <div><dt>Mean risk</dt><dd>{risk(leg.mean_risk)}</dd></div>
        <div><dt>Max risk</dt><dd>{risk(leg.max_risk)}</dd></div>
        {typeof leg.lit_share === 'number' && <div><dt>Lit</dt><dd>{percent(leg.lit_share)}</dd></div>}
        {typeof leg.main_road_share === 'number' && <div><dt>Main roads</dt><dd>{percent(leg.main_road_share)}</dd></div>}
      </dl>
    </button>
  )
}

export function RouteCards({ route, selected, onSelect }: Props) {
  return (
    <section aria-label="Routes">
      <div className="route-cards" role="radiogroup" aria-label="Route alternatives">
        {(['safe', 'fast'] as const).map((id) => (
          <Card key={id} id={id} leg={route[id]} selected={selected === id} onSelect={() => onSelect(id)} />
        ))}
      </div>
      <p className="comparison">{comparison(route)}</p>
      <p className="fine">Risk values are modelled, from 0 to 1. They are not probabilities.</p>
    </section>
  )
}
