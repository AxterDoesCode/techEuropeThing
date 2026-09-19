import { useEffect, useRef } from 'react'
import type { AreaResponse, Hotel, RouteResponse } from '../api'
import { starCount, subtypeLabel } from '../hotel'
import type { RequestState } from '../hooks/useRequest'
import { AreaSection } from './AreaSection'
import { HotelLink } from './HotelLink'
import { RiskMeter } from './RiskMeter'
import { RouteSection } from './RouteSection'

interface Props {
  hotel: Hotel
  area: RequestState<AreaResponse>
  route: RequestState<RouteResponse>
  pinned: boolean
  pinDisabled: boolean
  onTogglePin: (hotel: Hotel) => void
  onRetryArea: () => void
  onRetryRoute: () => void
  onClose: () => void
  /** Set on small screens, where the panel covers the map. */
  onShowMap?: () => void
}

/** Drawer beside the list on wide screens, full-screen sheet on small screens (see index.css). */
export function DetailPanel(props: Props) {
  const { hotel, area, route, pinned, pinDisabled, onTogglePin, onRetryArea, onRetryRoute, onClose, onShowMap } = props
  const heading = useRef<HTMLHeadingElement>(null)
  const stars = starCount(hotel)

  useEffect(() => {
    heading.current?.focus()
  }, [hotel.id])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside className="detail" aria-labelledby="detail-heading" data-testid="detail">
      <div className="detail__bar">
        <button type="button" className="button button--small button--ghost" onClick={onClose}>
          <span aria-hidden="true">← </span>Back to results
        </button>
        <button
          type="button"
          className="button button--small"
          aria-pressed={pinned}
          disabled={!pinned && pinDisabled}
          onClick={() => onTogglePin(hotel)}
        >
          {pinned ? 'Remove from compare' : 'Compare'}
        </button>
      </div>
      <div className="detail__body">
        <h2 id="detail-heading" ref={heading} tabIndex={-1}>
          {hotel.name}
        </h2>
        <p className="card__type">
          {subtypeLabel(hotel)}
          {stars !== null && ` · ${stars}★`}
        </p>
        {hotel.details.address && <p className="card__address">{hotel.details.address}</p>}
        <p className="detail__contact">
          {hotel.details.phone && (
            <a className="link" href={`tel:${hotel.details.phone.replace(/[^+\d]/g, '')}`}>
              {hotel.details.phone}
            </a>
          )}
          <HotelLink hotel={hotel} />
        </p>
        <RiskMeter risk={hotel.risk} />
        <p className="note">
          Modelled risk combines current events with police-recorded crime for the period shown below. It is a modelled value
          from 0 to 1, not a probability.
        </p>
        <AreaSection state={area} onRetry={onRetryArea} />
        <RouteSection station={hotel.nearest_station} state={route} onRetry={onRetryRoute} onShowMap={onShowMap} />
      </div>
    </aside>
  )
}
