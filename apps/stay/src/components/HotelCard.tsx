import type { Hotel } from '../api'
import { starCount, subtypeLabel } from '../hotel'
import { formatDistance, formatMinutes, straightLineMinutes } from '../risk'
import { HotelLink } from './HotelLink'
import { RiskMeter } from './RiskMeter'

interface Props {
  hotel: Hotel
  placeLabel: string
  selected: boolean
  hovered: boolean
  pinned: boolean
  pinDisabled: boolean
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  onTogglePin: (hotel: Hotel) => void
}

export function HotelCard(props: Props) {
  const { hotel, placeLabel, selected, hovered, pinned, pinDisabled, onSelect, onHover, onTogglePin } = props
  const stars = starCount(hotel)
  const station = hotel.nearest_station
  const className = ['card', selected ? 'is-selected' : '', hovered ? 'is-hovered' : ''].filter(Boolean).join(' ')

  return (
    <li
      className={className}
      data-hotel-id={hotel.id}
      onMouseEnter={() => onHover(hotel.id)}
      onMouseLeave={() => onHover(null)}
    >
      <div className="card__head">
        <h3 className="card__name">
          <button
            type="button"
            className="card__open"
            aria-pressed={selected}
            onClick={() => onSelect(hotel.id)}
            onFocus={() => onHover(hotel.id)}
            onBlur={() => onHover(null)}
          >
            {hotel.name}
          </button>
        </h3>
        <p className="card__type">
          {subtypeLabel(hotel)}
          {stars !== null && (
            <span className="card__stars" aria-label={`${stars} star rating from OpenStreetMap`}>
              {' · '}
              {stars}★
            </span>
          )}
        </p>
      </div>
      {hotel.details.address && <p className="card__address">{hotel.details.address}</p>}
      <ul className="card__facts">
        <li>
          {formatDistance(hotel.distance_m)} from {placeLabel}
        </li>
        {station ? (
          <li>
            {station.name} station {formatDistance(station.distance_m)} away, about{' '}
            {formatMinutes(straightLineMinutes(station.distance_m))} walk
          </li>
        ) : (
          <li>No station found nearby</li>
        )}
      </ul>
      <RiskMeter risk={hotel.risk} />
      <div className="card__actions">
        <button type="button" className="button button--small" onClick={() => onSelect(hotel.id)}>
          Area and walk details
        </button>
        <button
          type="button"
          className="button button--small button--ghost"
          aria-pressed={pinned}
          disabled={!pinned && pinDisabled}
          title={!pinned && pinDisabled ? 'Three hotels are already in the comparison' : undefined}
          onClick={() => onTogglePin(hotel)}
        >
          {pinned ? 'Remove from compare' : 'Compare'}
        </button>
        <HotelLink hotel={hotel} />
      </div>
    </li>
  )
}
