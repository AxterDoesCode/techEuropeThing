import type { Hotel, HotelSubtype } from './api'
import { SUBTYPES } from './urlState'

export function subtypeOf(hotel: Hotel): HotelSubtype {
  const raw = hotel.details.subtype ?? hotel.kind
  return SUBTYPES.some((s) => s.value === raw) ? (raw as HotelSubtype) : 'hotel'
}

export function subtypeLabel(hotel: Hotel): string {
  const subtype = subtypeOf(hotel)
  return SUBTYPES.find((s) => s.value === subtype)?.label ?? 'Hotel'
}

/** OpenStreetMap `stars` is free text ("4", "4S", "3.5"). Only a leading number from 1 to 5 is shown. */
export function starCount(hotel: Hotel): number | null {
  const value = Number.parseFloat(hotel.details.stars ?? '')
  return Number.isFinite(value) && value >= 1 && value <= 5 ? value : null
}

/** Only http(s) links from OpenStreetMap are rendered as links. */
export function websiteUrl(hotel: Hotel): string | null {
  const raw = hotel.details.website?.trim()
  if (!raw) return null
  const withScheme = /^[a-z][a-z0-9+.-]*:/i.test(raw) ? raw : `https://${raw}`
  try {
    const url = new URL(withScheme)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

export function webSearchUrl(hotel: Hotel): string {
  return `https://duckduckgo.com/?q=${encodeURIComponent(`${hotel.name} London`)}`
}
