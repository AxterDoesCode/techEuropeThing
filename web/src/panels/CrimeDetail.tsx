import { CRIME_QUERY_RADIUS_M, crimeCategoryLabel, type CrimeSummary } from '../map/crimeIndex'
import { formatCoord } from '../format'
import type { LngLat } from '../types'

interface Props {
  pos: LngLat
  summary: CrimeSummary
}

function comparison(summary: CrimeSummary): string | null {
  if (summary.percentile === null) return null
  const pct = Math.round(summary.percentile)
  if (pct >= 100) return 'Higher than all sampled London street locations with recorded crime'
  return `Higher than ${pct}% of London street locations with recorded crime`
}

// Popup content for a click on the crime layer: recorded crime within
// CRIME_QUERY_RADIUS_M of the clicked position
export function CrimeDetail({ pos, summary }: Props) {
  const compared = comparison(summary)
  return (
    <div className="detail crime-detail">
      <h3>Recorded street crime within {CRIME_QUERY_RADIUS_M} m</h3>
      <p className="meta">
        {formatCoord(pos.lng)}, {formatCoord(pos.lat)}
      </p>
      {summary.total === 0 ? (
        <p className="summary">No recorded street crimes within {CRIME_QUERY_RADIUS_M} m of this position.</p>
      ) : (
        <>
          <p className="total">
            <strong>{summary.total.toLocaleString('en-GB')}</strong> recorded {summary.total === 1 ? 'crime' : 'crimes'} at{' '}
            {summary.points} street {summary.points === 1 ? 'point' : 'points'}
            {compared && <span className="meta">{compared}</span>}
          </p>
          <h4>Most common categories <span className="meta">from the top 3 of each street point; not a complete count</span></h4>
          <dl>
            {summary.categories.map(([id, n]) => (
              <div key={id}><dt>{crimeCategoryLabel(id)}</dt><dd>{n}</dd></div>
            ))}
          </dl>
          <h4>Street points with the most crimes</h4>
          <dl>
            {summary.streets.map(([street, n]) => (
              <div key={street}><dt>{street}</dt><dd>{n}</dd></div>
            ))}
          </dl>
        </>
      )}
    </div>
  )
}
