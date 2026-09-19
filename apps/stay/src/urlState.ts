// The search, filters and selected hotel are stored in the query string so a link reproduces the view.

import type { HotelSort, HotelSubtype } from './api'

export const RADIUS_OPTIONS = [
  { value: 800, label: '800 m' },
  { value: 1500, label: '1.5 km' },
  { value: 3000, label: '3 km' },
] as const

export const DEFAULT_RADIUS = 1500

export const SUBTYPES: { value: HotelSubtype; label: string }[] = [
  { value: 'hotel', label: 'Hotel' },
  { value: 'hostel', label: 'Hostel' },
  { value: 'guest_house', label: 'Guest house' },
  { value: 'apartment', label: 'Apartment' },
]

export interface SearchPlace {
  label: string
  lat: number
  lng: number
}

export interface UrlState {
  place: SearchPlace | null
  radius: number
  sort: HotelSort
  /** Empty means every type is shown. */
  types: HotelSubtype[]
  websiteOnly: boolean
  hotelId: string | null
}

// Greater London scope of the platform; coordinates outside it return 422.
const BOUNDS = { west: -0.5104, south: 51.2868, east: 0.334, north: 51.6919 }

function isSubtype(value: string): value is HotelSubtype {
  return SUBTYPES.some((s) => s.value === value)
}

export function parseUrlState(search: string): UrlState {
  const q = new URLSearchParams(search)
  const lat = Number(q.get('lat'))
  const lng = Number(q.get('lng'))
  const hasPlace =
    q.has('lat') &&
    q.has('lng') &&
    Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    lat >= BOUNDS.south &&
    lat <= BOUNDS.north &&
    lng >= BOUNDS.west &&
    lng <= BOUNDS.east
  const radius = Number(q.get('r'))
  return {
    place: hasPlace ? { label: q.get('q') || 'Selected point', lat, lng } : null,
    radius: RADIUS_OPTIONS.some((o) => o.value === radius) ? radius : DEFAULT_RADIUS,
    sort: q.get('sort') === 'distance' ? 'distance' : 'safety',
    types: (q.get('types') ?? '').split(',').filter(isSubtype),
    websiteOnly: q.get('web') === '1',
    hotelId: q.get('hotel'),
  }
}

export function serialiseUrlState(state: UrlState): string {
  const q = new URLSearchParams()
  if (state.place) {
    q.set('q', state.place.label)
    q.set('lat', state.place.lat.toFixed(5))
    q.set('lng', state.place.lng.toFixed(5))
  }
  if (state.radius !== DEFAULT_RADIUS) q.set('r', String(state.radius))
  if (state.sort !== 'safety') q.set('sort', state.sort)
  if (state.types.length > 0) q.set('types', state.types.join(','))
  if (state.websiteOnly) q.set('web', '1')
  if (state.place && state.hotelId) q.set('hotel', state.hotelId)
  const text = q.toString()
  return text ? `?${text}` : ''
}
