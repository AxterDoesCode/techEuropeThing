// Place search for the text box. Photon is a public OpenStreetMap geocoder; the platform has none.

import type { LngLat } from './api'

const PHOTON_URL = 'https://photon.komoot.io/api/'
const LONDON_BBOX = '-0.5104,51.2868,0.3340,51.6919'

export interface PlaceSuggestion {
  key: string
  name: string
  context: string
  center: LngLat
}

interface PhotonFeature {
  geometry: { coordinates: [number, number] }
  properties: {
    osm_id?: number
    osm_type?: string
    name?: string
    street?: string
    housenumber?: string
    district?: string
    city?: string
    postcode?: string
  }
}

interface PhotonResponse {
  features: PhotonFeature[]
}

function toSuggestion(feature: PhotonFeature, index: number): PlaceSuggestion | null {
  const p = feature.properties
  const streetLine = [p.housenumber, p.street].filter(Boolean).join(' ')
  const name = p.name ?? streetLine
  if (!name) return null
  const context = [p.name ? streetLine : '', p.district, p.city, p.postcode]
    .filter((part): part is string => Boolean(part) && part !== name)
    .filter((part, i, all) => all.indexOf(part) === i)
    .join(', ')
  const [lng, lat] = feature.geometry.coordinates
  return { key: `${p.osm_type ?? ''}${p.osm_id ?? index}`, name, context, center: [lng, lat] }
}

export async function searchPlaces(text: string, signal: AbortSignal): Promise<PlaceSuggestion[]> {
  const query = new URLSearchParams({ q: text, bbox: LONDON_BBOX, limit: '6', lang: 'en' })
  const response = await fetch(`${PHOTON_URL}?${query}`, { signal })
  if (!response.ok) throw new Error(`Place search failed with status ${response.status}`)
  const data = (await response.json()) as PhotonResponse
  return data.features
    .map(toSuggestion)
    .filter((s): s is PlaceSuggestion => s !== null)
}
