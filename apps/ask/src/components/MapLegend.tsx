import type { RouteLeg } from '../api'
import type { MapContext } from '../mapContext'
import type { ContextData } from '../useContextData'

function legText(leg: RouteLeg): string {
  const parts: string[] = []
  if (Number.isFinite(leg.length_m)) parts.push(`${(leg.length_m / 1000).toFixed(2)} km`)
  if (Number.isFinite(leg.duration_min)) parts.push(`${Math.round(leg.duration_min)} min`)
  if (Number.isFinite(leg.mean_risk)) parts.push(`mean modelled risk ${leg.mean_risk.toFixed(2)}`)
  if (leg.lit_share !== undefined) parts.push(`${Math.round(leg.lit_share * 100)}% on lit streets`)
  return parts.join(', ')
}

interface Props {
  context: MapContext
  data: ContextData | null
  loading: boolean
}

export function MapLegend({ context, data, loading }: Props) {
  const route = data?.routes[0]
  return (
    <div className="map-legend" aria-label="Map legend">
      <ul>
        {context.places.length > 0 && (
          <li>
            <span className="swatch swatch-place" aria-hidden="true" /> Place found by the assistant
          </li>
        )}
        {context.areas.length > 0 && (
          <li>
            <span className="swatch swatch-area" aria-hidden="true" /> Area the data was read for
          </li>
        )}
        {route && (
          <>
            <li>
              <span className="swatch swatch-lower" aria-hidden="true" /> Lower-risk route
              <span className="legend-detail">{legText(route.safe)}</span>
            </li>
            <li>
              <span className="swatch swatch-shortest" aria-hidden="true" /> Shortest route
              <span className="legend-detail">{legText(route.fast)}</span>
            </li>
          </>
        )}
        {context.hotelSearches.length > 0 && (
          <li>
            <span className="swatch swatch-hotel" aria-hidden="true" /> Hotel search area
            {data && data.hotels.length > 0 ? ` and first ${data.hotels.length} results` : ''}
          </li>
        )}
      </ul>
      {route && <p className="legend-note">Routes are requested again for the map; figures can differ from the answer.</p>}
      {loading && <p className="legend-note">Loading map data…</p>}
      {data && data.failed > 0 && <p className="legend-note">Some map data could not be loaded.</p>}
    </div>
  )
}
