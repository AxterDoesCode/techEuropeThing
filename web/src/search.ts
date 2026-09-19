// Place search for Greater London, backed by the Photon geocoder
// (https://photon.komoot.io). Photon permits search-as-you-type requests;
// Nominatim's usage policy does not.

export interface PlaceResult {
  id: string
  label: string
  detail: string
  kind: string
  lng: number
  lat: number
  // west, south, east, north
  bbox: [number, number, number, number] | null
}

const PHOTON_URL = 'https://photon.komoot.io/api/'
const POSTCODES_URL = 'https://api.postcodes.io/postcodes/'

// Greater London: west, south, east, north
const LONDON = { west: -0.5104, south: 51.2868, east: 0.334, north: 51.6919 }
const CENTRE = { lng: -0.1278, lat: 51.5074 }

const MIN_QUERY_LENGTH = 3
const FETCH_LIMIT = 15
const MAX_RESULTS = 8
const AREA_LIMIT = 4
const AREA_LAYERS = ['district', 'city', 'county']

const FULL_POSTCODE = /^([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})$/i
const COORDINATE_PAIR = /^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/

const DROPPED_DETAIL = new Set(['england', 'united kingdom', 'london', 'greater london'])

interface PhotonProperties {
  osm_type?: string
  osm_id?: number
  osm_key?: string
  osm_value?: string
  type?: string
  name?: string
  housenumber?: string
  street?: string
  locality?: string
  district?: string
  city?: string
  county?: string
  postcode?: string
  // Photon order: west, north, east, south
  extent?: [number, number, number, number]
}

interface PhotonFeature {
  properties: PhotonProperties
  geometry: { type: string; coordinates: [number, number] }
}

const inLondon = (lng: number, lat: number) =>
  lng >= LONDON.west && lng <= LONDON.east && lat >= LONDON.south && lat <= LONDON.north

// Returns null when the query is not a coordinate pair, 'outside' when it is a
// pair that does not fall inside the London bounding box in either order.
function parseCoordinates(query: string): PlaceResult | 'outside' | null {
  const match = COORDINATE_PAIR.exec(query)
  if (!match) return null
  const a = Number(match[1])
  const b = Number(match[2])
  // Longitude and latitude ranges in London do not overlap, so at most one order fits.
  const pos = inLondon(a, b) ? { lng: a, lat: b } : inLondon(b, a) ? { lng: b, lat: a } : null
  if (!pos) return 'outside'
  return {
    id: `coord:${pos.lng},${pos.lat}`,
    label: `${pos.lng.toFixed(5)}, ${pos.lat.toFixed(5)}`,
    detail: 'Longitude, latitude',
    kind: 'coordinates',
    lng: pos.lng,
    lat: pos.lat,
    bbox: null,
  }
}

function normalisePostcode(query: string): string | null {
  const match = FULL_POSTCODE.exec(query.trim())
  return match ? `${match[1].toUpperCase()} ${match[2].toUpperCase()}` : null
}

const BOROUGH_NAME = /^(London Borough of|Royal Borough of|City of) /

function kindOf(p: PhotonProperties): string {
  const key = p.osm_key ?? ''
  const value = p.osm_value ?? ''
  if (key === 'place' && value === 'postcode') return 'postcode'
  if (key === 'boundary' && BOROUGH_NAME.test(p.name ?? '')) return 'borough'
  if (key === 'railway' || key === 'public_transport') {
    return value === 'platform' ? 'platform' : value === 'tram_stop' ? 'tram stop' : 'station'
  }
  if (key === 'highway') return value === 'bus_stop' ? 'bus stop' : 'street'
  if (key === 'place' || key === 'boundary') {
    return value === 'square' ? 'square' : 'area'
  }
  if (p.type === 'street') return 'street'
  if (p.type === 'district' || p.type === 'city' || p.type === 'county' || p.type === 'locality') return 'area'
  if (!p.name && p.housenumber) return 'address'
  if (key === 'leisure' && value === 'park') return 'park'
  if (value && value !== 'yes') return value.replace(/_/g, ' ')
  if (key === 'building') return 'building'
  return 'place'
}

// Order of result groups; Photon's own order is kept inside each group.
function rank(kind: string): number {
  if (kind === 'postcode' || kind === 'borough' || kind === 'area') return 0
  if (kind === 'street' || kind === 'station' || kind === 'square' || kind === 'park') return 1
  return 2
}

function toPlace(feature: PhotonFeature): PlaceResult | null {
  const p = feature.properties
  const [lng, lat] = feature.geometry.coordinates
  if (!Number.isFinite(lng) || !Number.isFinite(lat) || !inLondon(lng, lat)) return null

  const address = [p.housenumber, p.street].filter(Boolean).join(' ')
  const label = p.name ?? address
  if (!label) return null
  const kind = kindOf(p)

  const parts = [p.name ? address : '', p.locality, p.district, p.city, p.county, p.postcode]
  const seen = new Set([label.toLowerCase()])
  const detail: string[] = []
  for (const part of parts) {
    if (!part) continue
    const lower = part.toLowerCase()
    if (seen.has(lower) || DROPPED_DETAIL.has(lower)) continue
    seen.add(lower)
    detail.push(part)
  }

  // Photon derives a postcode extent from a fixed radius, not from the postcode
  // area, so it is not used as a bounding box.
  let bbox: PlaceResult['bbox'] = null
  if (p.extent && p.extent.length === 4 && kind !== 'postcode') {
    const [west, north, east, south] = p.extent
    bbox = [Math.min(west, east), Math.min(south, north), Math.max(west, east), Math.max(south, north)]
  }

  return {
    id: p.osm_type && p.osm_id ? `${p.osm_type}${p.osm_id}` : `${label}@${lng},${lat}`,
    label,
    detail: detail.join(', ') || 'London',
    kind,
    lng,
    lat,
    bbox,
  }
}

// One entry per label + kind + surrounding area. A long street is returned by
// Photon as several segments that differ only in postcode; those are merged.
function dedupe(places: PlaceResult[], features: Map<string, PhotonProperties>): PlaceResult[] {
  const seen = new Set<string>()
  const ids = new Set<string>()
  const out: PlaceResult[] = []
  for (const place of places) {
    const p = features.get(place.id)
    const area = [p?.street, p?.locality, p?.district].filter(Boolean).join('|')
    const key = `${place.label}|${place.kind}|${area}`.toLowerCase()
    const exact = `${place.label}|${place.detail}`.toLowerCase()
    if (seen.has(key) || seen.has(exact) || ids.has(place.id)) continue
    seen.add(key)
    seen.add(exact)
    ids.add(place.id)
    out.push(place)
  }
  return out
}

async function getJson(url: string, service: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(url, { signal, headers: { Accept: 'application/json' } })
  } catch (cause) {
    if (signal?.aborted) throw cause
    throw new Error(`Place search failed: ${service} could not be reached`, { cause })
  }
  if (response.status === 404) return null
  if (response.status === 429) throw new Error(`Place search failed: ${service} rate limit reached, retry shortly`)
  if (!response.ok) throw new Error(`Place search failed: ${service} returned HTTP ${response.status}`)
  try {
    return await response.json()
  } catch (cause) {
    throw new Error(`Place search failed: ${service} returned an unreadable response`, { cause })
  }
}

type Tagged = { place: PlaceResult; properties: PhotonProperties }

async function photonRequest(query: string, limit: number, layers: string[], signal?: AbortSignal): Promise<Tagged[]> {
  const params = new URLSearchParams({
    q: query,
    limit: String(limit),
    lang: 'en',
    bbox: `${LONDON.west},${LONDON.south},${LONDON.east},${LONDON.north}`,
    lat: String(CENTRE.lat),
    lon: String(CENTRE.lng),
  })
  for (const layer of layers) params.append('layer', layer)
  const body = (await getJson(`${PHOTON_URL}?${params}`, 'Photon', signal)) as { features?: PhotonFeature[] } | null
  if (!body || !Array.isArray(body.features)) throw new Error('Place search failed: Photon returned an unexpected response')

  const out: Tagged[] = []
  for (const feature of body.features) {
    if (!feature?.properties || !Array.isArray(feature.geometry?.coordinates)) continue
    const place = toPlace(feature)
    if (place) out.push({ place, properties: feature.properties })
  }
  return out
}

// Two requests per query: the general search, and one restricted to area
// layers. The general search ranks stations and buildings above boroughs
// ("hackney" does not return "London Borough of Hackney" in its first 15
// results), so the area request supplies them. A failed area request is ignored.
async function searchPhoton(query: string, signal?: AbortSignal, withAreas = true): Promise<PlaceResult[]> {
  const [general, areas] = await Promise.all([
    photonRequest(query, FETCH_LIMIT, [], signal),
    withAreas
      ? photonRequest(query, AREA_LIMIT, AREA_LAYERS, signal).catch((cause: unknown) => {
          if (signal?.aborted) throw cause
          return [] as Tagged[]
        })
      : Promise.resolve([] as Tagged[]),
  ])
  const properties = new Map<string, PhotonProperties>()
  const places: PlaceResult[] = []
  for (const { place, properties: p } of [...areas, ...general]) {
    if (!properties.has(place.id)) properties.set(place.id, p)
    places.push(place)
  }
  // Array.prototype.sort is stable, so Photon's relevance order is kept within a group.
  places.sort((a, b) => rank(a.kind) - rank(b.kind))
  return dedupe(places, properties)
}

interface PostcodesIoResult {
  postcode: string
  longitude: number | null
  latitude: number | null
  admin_district: string | null
  admin_ward: string | null
}

async function lookupPostcode(postcode: string, signal?: AbortSignal): Promise<PlaceResult[]> {
  const url = `${POSTCODES_URL}${encodeURIComponent(postcode)}`
  const body = (await getJson(url, 'postcodes.io', signal)) as { result?: PostcodesIoResult } | null
  const r = body?.result
  if (!r || r.longitude == null || r.latitude == null || !inLondon(r.longitude, r.latitude)) return []
  return [{
    id: `postcode:${r.postcode}`,
    label: r.postcode,
    detail: [r.admin_ward, r.admin_district].filter(Boolean).join(', ') || 'London',
    kind: 'postcode',
    lng: r.longitude,
    lat: r.latitude,
    bbox: null,
  }]
}

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<PlaceResult[]> {
  const q = query.trim().replace(/\s+/g, ' ')
  if (q.length < MIN_QUERY_LENGTH) return []

  const coordinates = parseCoordinates(q)
  if (coordinates) return coordinates === 'outside' ? [] : [coordinates]

  const postcode = normalisePostcode(q)
  if (postcode) {
    // Photon also returns near matches (N17 9AG for N1 9AG); keep the exact one only.
    // postcodes.io is used when Photon has no exact match or is unavailable.
    let exact: PlaceResult[] = []
    try {
      exact = (await searchPhoton(postcode, signal, false)).filter((p) => p.kind === 'postcode' && p.label === postcode)
    } catch (cause) {
      if (signal?.aborted) throw cause
    }
    return exact.length > 0 ? exact.slice(0, 1) : lookupPostcode(postcode, signal)
  }

  return (await searchPhoton(q, signal)).slice(0, MAX_RESULTS)
}
