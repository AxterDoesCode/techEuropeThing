import type { CrimeRow, LngLat } from '../types'

// Radius of the summary shown when the map is clicked
export const CRIME_QUERY_RADIUS_M = 150

const M_PER_DEG_LAT = 111_320
// Longitude scale at the latitude of London; the error across Greater London is below 1 %
const M_PER_DEG_LNG = M_PER_DEG_LAT * Math.cos((51.5 * Math.PI) / 180)
// Every n-th row is used for the reference distribution
const SAMPLE_STEP = 10

// police.uk category ids
const CRIME_CATEGORY_LABEL: Record<string, string> = {
  'anti-social-behaviour': 'Anti-social behaviour',
  'bicycle-theft': 'Bicycle theft',
  burglary: 'Burglary',
  'criminal-damage-arson': 'Criminal damage and arson',
  drugs: 'Drugs',
  'other-crime': 'Other crime',
  'other-theft': 'Other theft',
  'possession-of-weapons': 'Possession of weapons',
  'public-order': 'Public order',
  robbery: 'Robbery',
  shoplifting: 'Shoplifting',
  'theft-from-the-person': 'Theft from the person',
  'vehicle-crime': 'Vehicle crime',
  'violent-crime': 'Violence and sexual offences',
}

export function crimeCategoryLabel(id: string): string {
  if (CRIME_CATEGORY_LABEL[id]) return CRIME_CATEGORY_LABEL[id]
  const text = id.replace(/-/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// month is 'YYYY-MM'
export function monthLabel(month: string): string {
  return new Date(`${month}-01T00:00:00Z`).toLocaleString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })
}

export interface CrimeIndex {
  rows: CrimeRow[]
  /** row numbers per grid cell of CRIME_QUERY_RADIUS_M, keyed by cellKey */
  cells: Map<number, number[]>
  /** sorted totals within the query radius around every SAMPLE_STEP-th row */
  sampleTotals: number[]
}

export interface CrimeSummary {
  /** recorded crimes at street points within the radius */
  total: number
  /** number of street points within the radius */
  points: number
  /** summed from the top categories of each row, so it is not a complete count */
  categories: [string, number][]
  streets: [string, number][]
  /** share (0-100) of the sampled street points with a lower total; null without data */
  percentile: number | null
}

const cellX = (lng: number) => Math.floor((lng * M_PER_DEG_LNG) / CRIME_QUERY_RADIUS_M)
const cellY = (lat: number) => Math.floor((lat * M_PER_DEG_LAT) / CRIME_QUERY_RADIUS_M)
const cellKey = (x: number, y: number) => x * 1_000_000 + y

// Rows within the radius: the 3 x 3 cells around the position contain all of them
function rowsNear(cells: Map<number, number[]>, rows: CrimeRow[], pos: LngLat): CrimeRow[] {
  const x = cellX(pos.lng)
  const y = cellY(pos.lat)
  const found: CrimeRow[] = []
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      for (const i of cells.get(cellKey(x + dx, y + dy)) ?? []) {
        const row = rows[i]
        const mx = (row[0] - pos.lng) * M_PER_DEG_LNG
        const my = (row[1] - pos.lat) * M_PER_DEG_LAT
        if (mx * mx + my * my <= CRIME_QUERY_RADIUS_M * CRIME_QUERY_RADIUS_M) found.push(row)
      }
    }
  }
  return found
}

export function buildCrimeIndex(rows: CrimeRow[]): CrimeIndex {
  const cells = new Map<number, number[]>()
  rows.forEach((row, i) => {
    const key = cellKey(cellX(row[0]), cellY(row[1]))
    const list = cells.get(key)
    if (list) list.push(i)
    else cells.set(key, [i])
  })
  const sampleTotals: number[] = []
  for (let i = 0; i < rows.length; i += SAMPLE_STEP) {
    const near = rowsNear(cells, rows, { lng: rows[i][0], lat: rows[i][1] })
    sampleTotals.push(near.reduce((sum, r) => sum + r[2], 0))
  }
  sampleTotals.sort((a, b) => a - b)
  return { rows, cells, sampleTotals }
}

const topEntries = (counts: Map<string, number>, n: number): [string, number][] =>
  [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, n)

export function summariseCrime(index: CrimeIndex, pos: LngLat): CrimeSummary {
  const near = rowsNear(index.cells, index.rows, pos)
  const categories = new Map<string, number>()
  const streets = new Map<string, number>()
  let total = 0
  for (const row of near) {
    total += row[2]
    streets.set(row[4], (streets.get(row[4]) ?? 0) + row[2])
    for (const [id, n] of Object.entries(row[5] ?? {})) categories.set(id, (categories.get(id) ?? 0) + n)
  }
  // What is compared: the total within 150 m of the clicked position against the
  // totals within 150 m of every 10th street point of the dataset. Every street
  // point has at least one recorded crime, so the reference is "street locations
  // in London where crime was recorded this month", not all of London's area;
  // places without any recorded crime are not part of it.
  let below = 0
  while (below < index.sampleTotals.length && index.sampleTotals[below] < total) below++
  const percentile = index.sampleTotals.length > 0 ? (100 * below) / index.sampleTotals.length : null
  return { total, points: near.length, categories: topEntries(categories, 5), streets: topEntries(streets, 3), percentile }
}
