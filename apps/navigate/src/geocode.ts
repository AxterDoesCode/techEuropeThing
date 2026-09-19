// Place search and reverse geocoding with Photon (https://photon.komoot.io), which permits
// search-as-you-type requests. Results are restricted to the Greater London bounding box.
import type { LngLat } from './api'
import { LONDON, LONDON_CENTRE, inLondon } from './geo'

export interface Place {
  id: string
  name: string
  detail: string
  point: LngLat
}

interface PhotonProperties {
  osm_type?: string
  osm_id?: number
  name?: string
  housenumber?: string
  street?: string
  locality?: string
  district?: string
  city?: string
  postcode?: string
}

interface PhotonFeature {
  properties?: PhotonProperties
  geometry?: { coordinates?: [number, number] }
}

const PHOTON = 'https://photon.komoot.io'
export const MIN_QUERY_LENGTH = 3

function toPlace(feature: PhotonFeature): Place | null {
  const p = feature.properties
  const c = feature.geometry?.coordinates
  if (!p || !c || !Number.isFinite(c[0]) || !Number.isFinite(c[1]) || !inLondon(c)) return null
  const address = [p.housenumber, p.street].filter(Boolean).join(' ')
  const name = p.name ?? address
  if (!name) return null
  const parts = [p.name ? address : '', p.locality, p.district, p.postcode]
  const seen = new Set([name.toLowerCase()])
  const detail: string[] = []
  for (const part of parts) {
    if (!part || seen.has(part.toLowerCase())) continue
    seen.add(part.toLowerCase())
    detail.push(part)
  }
  return {
    id: p.osm_type && p.osm_id ? `${p.osm_type}${p.osm_id}` : `${name}@${c[0]},${c[1]}`,
    name,
    detail: detail.join(', ') || 'London',
    point: [c[0], c[1]],
  }
}

async function photon(path: string, params: URLSearchParams, signal?: AbortSignal): Promise<Place[]> {
  let response: Response
  try {
    response = await fetch(`${PHOTON}${path}?${params}`, { signal, headers: { Accept: 'application/json' } })
  } catch (cause) {
    if (signal?.aborted) throw cause
    throw new Error('Place search could not be reached')
  }
  if (response.status === 429) throw new Error('Place search rate limit reached; retry shortly')
  if (!response.ok) throw new Error(`Place search returned HTTP ${response.status}`)
  const body = (await response.json().catch(() => null)) as { features?: PhotonFeature[] } | null
  if (!body || !Array.isArray(body.features)) throw new Error('Place search returned an unexpected response')
  const seen = new Set<string>()
  const out: Place[] = []
  for (const feature of body.features) {
    const place = toPlace(feature)
    if (!place) continue
    // A long street is returned as several segments with the same name and district.
    const key = `${place.name}|${place.detail}`.toLowerCase()
    if (seen.has(place.id) || seen.has(key)) continue
    seen.add(place.id)
    seen.add(key)
    out.push(place)
  }
  return out
}

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<Place[]> {
  const q = query.trim().replace(/\s+/g, ' ')
  if (q.length < MIN_QUERY_LENGTH) return []
  const params = new URLSearchParams({
    q,
    limit: '8',
    lang: 'en',
    bbox: `${LONDON.west},${LONDON.south},${LONDON.east},${LONDON.north}`,
    lat: String(LONDON_CENTRE[1]),
    lon: String(LONDON_CENTRE[0]),
  })
  return (await photon('/api/', params, signal)).slice(0, 6)
}

// Name of the nearest mapped object, or null when there is none or the request fails.
export async function reverseGeocode(point: LngLat, signal?: AbortSignal): Promise<string | null> {
  const params = new URLSearchParams({ lon: String(point[0]), lat: String(point[1]), limit: '1', lang: 'en' })
  try {
    const places = await photon('/reverse', params, signal)
    return places[0]?.name ?? null
  } catch (cause) {
    if (signal?.aborted) throw cause
    return null
  }
}
