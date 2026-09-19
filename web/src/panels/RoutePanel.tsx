import { useState } from 'react'
import { fetchRoute, ROUTING_AVAILABLE } from '../api'
import type { RouteFields } from '../route'
import type { LngLat, RouteEndpoint, RouteLeg, RouteResult } from '../types'

const DEFAULT_ALPHA = 4

interface Props {
  fields: RouteFields
  origin: LngLat | null
  destination: LngLat | null
  picking: RouteEndpoint | null
  route: RouteResult | null
  onChange: (fields: RouteFields) => void
  onPick: (endpoint: RouteEndpoint | null) => void
  onRoute: (route: RouteResult | null) => void
}

const LABEL: Record<RouteEndpoint, string> = { origin: 'From', destination: 'To' }

const km = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`)

function LegRow({ name, leg }: { name: 'fast' | 'safe'; leg: RouteLeg }) {
  return (
    <tr>
      <th><span className={`swatch ${name}`} />{name === 'fast' ? 'Fast' : 'Safe'}</th>
      <td>{km(leg.length_m)}</td>
      <td>{Math.round(leg.duration_min)} min</td>
      <td>{leg.mean_risk.toFixed(3)}</td>
      <td>{leg.max_risk.toFixed(3)}</td>
    </tr>
  )
}

// Walking route request: two endpoints (typed or picked on the map), the risk
// weight alpha, and a comparison of the shortest route with the risk-weighted one.
export function RoutePanel({ fields, origin, destination, picking, route, onChange, onPick, onRoute }: Props) {
  const [alpha, setAlpha] = useState(DEFAULT_ALPHA)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!ROUTING_AVAILABLE) {
    return (
      <section className="panel route">
        <h2>Route</h2>
        <p className="meta">
          Routing is computed by the backend. This page is showing static sample data; start it with VITE_API_BASE set
          to use routing.
        </p>
      </section>
    )
  }

  const submit = () => {
    if (!origin || !destination) return
    setLoading(true)
    setError(null)
    onPick(null)
    fetchRoute(origin, destination, alpha)
      .then(onRoute)
      .catch((e: unknown) => {
        onRoute(null)
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => setLoading(false))
  }

  return (
    <section className="panel route">
      <h2>Walking route</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        {(['origin', 'destination'] as const).map((end) => (
          <div className="endpoint" key={end}>
            <label>
              {LABEL[end]}
              <input
                type="text" inputMode="decimal" placeholder="lng, lat" value={fields[end]}
                aria-invalid={fields[end] !== '' && !(end === 'origin' ? origin : destination)}
                onChange={(e) => onChange({ ...fields, [end]: e.target.value })}
              />
            </label>
            <button
              type="button" className={picking === end ? 'pick active' : 'pick'} aria-pressed={picking === end}
              onClick={() => onPick(picking === end ? null : end)}
            >
              {picking === end ? 'Click the map…' : 'Pick on map'}
            </button>
          </div>
        ))}
        <label className="alpha">
          <span>Safety preference <code>{alpha.toFixed(1)}</code></span>
          <input type="range" min={0} max={10} step={0.5} value={alpha} onChange={(e) => setAlpha(Number(e.target.value))} />
          <span className="scale-labels"><span>shortest</span><span>avoid risk</span></span>
        </label>
        <button type="submit" className="go" disabled={!origin || !destination || loading}>
          {loading ? 'Routing…' : 'Route'}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {route && (
        <div className="result">
          <table>
            <thead>
              <tr><th /><th>Distance</th><th>Time</th><th>Mean risk</th><th>Max risk</th></tr>
            </thead>
            <tbody>
              <LegRow name="fast" leg={route.fast} />
              <LegRow name="safe" leg={route.safe} />
            </tbody>
          </table>
          <p className="meta">
            Safe route: {route.extra_distance_m >= 0 ? '+' : '−'}{km(Math.abs(route.extra_distance_m))},{' '}
            {(route.risk_reduction * 100).toFixed(0)}% lower mean risk (alpha {route.alpha}). {route.attribution}
          </p>
        </div>
      )}
    </section>
  )
}
