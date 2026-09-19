import { useState } from 'react'
import type { Hotel } from '../api'
import { useHotelArea, useStationWalk } from '../hooks/useHotelData'
import { crimePeriodLabel, formatMinutes, formatScore, percentileNumber, riskClass } from '../risk'

export const MAX_PINNED = 3

function CrimesCell({ hotel }: { hotel: Hotel }) {
  const area = useHotelArea(hotel)
  if (area.status === 'loading') return <span className="muted">Loading…</span>
  if (area.status === 'error') return <span className="muted">Not available</span>
  if (area.status !== 'success' || !area.data.crime) return <span className="muted">No data</span>
  return (
    <>
      {area.data.crime.recorded_crimes.toLocaleString('en-GB')}
      <span className="muted compare__period"> {crimePeriodLabel(area.data.crime)}</span>
    </>
  )
}

function WalkCell({ hotel }: { hotel: Hotel }) {
  const route = useStationWalk(hotel)
  if (!hotel.nearest_station) return <span className="muted">No station nearby</span>
  if (route.status === 'loading') return <span className="muted">Loading…</span>
  if (route.status === 'error') {
    return <span className="muted">{route.error.status === 422 ? 'Outside the routing area' : 'Not available'}</span>
  }
  if (route.status !== 'success') return null
  return (
    <>
      {formatMinutes(route.data.fast.duration_min)}
      <span className="muted"> from {hotel.nearest_station.name}</span>
    </>
  )
}

interface Props {
  pinned: Hotel[]
  onRemove: (hotel: Hotel) => void
  onSelect: (hotel: Hotel) => void
  onClear: () => void
}

export function CompareTray({ pinned, onRemove, onSelect, onClear }: Props) {
  const [open, setOpen] = useState(true)
  if (pinned.length === 0) return null
  return (
    <section className="compare" aria-label="Compare hotels">
      <div className="compare__bar">
        <button type="button" className="compare__toggle" aria-expanded={open} aria-controls="compare-table" onClick={() => setOpen(!open)}>
          <span aria-hidden="true">{open ? '▾' : '▸'}</span> Compare ({pinned.length} of {MAX_PINNED})
        </button>
        <button type="button" className="button button--small button--ghost" onClick={onClear}>
          Clear
        </button>
      </div>
      {open && (
        <div className="compare__scroll" id="compare-table">
          <table className="compare__table">
            <thead>
              <tr>
                <td />
                {pinned.map((hotel) => (
                  <th key={hotel.id} scope="col">
                    <button type="button" className="compare__name" onClick={() => onSelect(hotel)}>
                      {hotel.name}
                    </button>
                    <button type="button" className="compare__remove" aria-label={`Remove ${hotel.name} from compare`} onClick={() => onRemove(hotel)}>
                      ×
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th scope="row">Modelled risk within 300 m</th>
                {pinned.map((hotel) => {
                  const cls = riskClass(hotel.risk?.mean_score)
                  return (
                    <td key={hotel.id}>
                      <span className="risk-meter__value" style={{ background: cls.fill, color: cls.text }}>
                        {hotel.risk ? formatScore(hotel.risk.mean_score) : '–'}
                      </span>
                    </td>
                  )
                })}
              </tr>
              <tr>
                <th scope="row">Higher than this share of London</th>
                {pinned.map((hotel) => (
                  <td key={hotel.id}>{hotel.risk ? `${percentileNumber(hotel.risk.london_percentile)}%` : '–'}</td>
                ))}
              </tr>
              <tr>
                <th scope="row">Recorded crimes within 300 m</th>
                {pinned.map((hotel) => (
                  <td key={hotel.id}>
                    <CrimesCell hotel={hotel} />
                  </td>
                ))}
              </tr>
              <tr>
                <th scope="row">Shortest walk from station</th>
                {pinned.map((hotel) => (
                  <td key={hotel.id}>
                    <WalkCell hotel={hotel} />
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
