import { useEffect, useRef } from 'react'
import type { Hotel } from '../api'
import { HotelCard } from './HotelCard'

interface Props {
  hotels: Hotel[]
  placeLabel: string
  selectedId: string | null
  hoveredId: string | null
  pinnedIds: string[]
  pinDisabled: boolean
  onSelect: (id: string) => void
  onHover: (id: string | null) => void
  onTogglePin: (hotel: Hotel) => void
}

export function HotelList(props: Props) {
  const { hotels, selectedId, hoveredId, pinnedIds, ...rest } = props
  const list = useRef<HTMLUListElement>(null)

  // A hotel selected on the map is scrolled into view in the list.
  useEffect(() => {
    if (!selectedId || !list.current) return
    const card = list.current.querySelector(`[data-hotel-id="${CSS.escape(selectedId)}"]`)
    card?.scrollIntoView({ block: 'nearest' })
  }, [selectedId])

  return (
    <ul className="cards" ref={list} aria-label="Hotels">
      {hotels.map((hotel) => (
        <HotelCard
          key={hotel.id}
          hotel={hotel}
          selected={hotel.id === selectedId}
          hovered={hotel.id === hoveredId}
          pinned={pinnedIds.includes(hotel.id)}
          {...rest}
        />
      ))}
    </ul>
  )
}
