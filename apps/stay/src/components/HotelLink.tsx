import type { Hotel } from '../api'
import { webSearchUrl, websiteUrl } from '../hotel'

/** The hotel's own website when OpenStreetMap has one, otherwise a web search for its name. */
export function HotelLink({ hotel }: { hotel: Hotel }) {
  const website = websiteUrl(hotel)
  return (
    <a
      className="link"
      href={website ?? webSearchUrl(hotel)}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
    >
      {website ? 'Hotel website' : 'Search this hotel'}
      <span className="visually-hidden"> ({hotel.name}, opens in a new tab)</span>
      <span aria-hidden="true"> ↗</span>
    </a>
  )
}
