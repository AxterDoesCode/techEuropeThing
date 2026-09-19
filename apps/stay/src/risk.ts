// Colour scale and number formatting shared by cards, the map and the compare tray.

export interface RiskClass {
  /** Upper bound of mean_score for this class (exclusive), 1.01 for the last. */
  below: number
  label: string
  fill: string
  /** Text colour with sufficient contrast on `fill`. */
  text: string
}

// Five classes of the viridis sequence, light to dark with rising score. Viridis is
// monotonic in lightness, so the order is readable with colour-vision deficiency and in
// greyscale. The number is always shown next to the colour.
export const RISK_CLASSES: RiskClass[] = [
  { below: 0.1, label: 'under 0.10', fill: '#fde725', text: '#1b1b1b' },
  { below: 0.2, label: '0.10 to 0.19', fill: '#5ec962', text: '#10240f' },
  { below: 0.3, label: '0.20 to 0.29', fill: '#21918c', text: '#ffffff' },
  { below: 0.45, label: '0.30 to 0.44', fill: '#3b528b', text: '#ffffff' },
  { below: 1.01, label: '0.45 and over', fill: '#440154', text: '#ffffff' },
]

const NO_DATA: RiskClass = { below: 0, label: 'no data', fill: '#c9ced6', text: '#1b1b1b' }

export function riskClass(score: number | null | undefined): RiskClass {
  if (score === null || score === undefined || Number.isNaN(score)) return NO_DATA
  return RISK_CLASSES.find((c) => score < c.below) ?? RISK_CLASSES[RISK_CLASSES.length - 1]
}

export function formatScore(score: number): string {
  return score.toFixed(2)
}

export function percentileNumber(percentile: number): number {
  return Math.round(percentile * 100)
}

export function percentileSentence(percentile: number): string {
  return `Modelled risk higher than ${percentileNumber(percentile)}% of London`
}

export function formatDistance(metres: number): string {
  if (metres < 1000) return `${Math.round(metres / 10) * 10} m`
  return `${(metres / 1000).toFixed(1)} km`
}

export function formatMinutes(minutes: number): string {
  return `${Math.max(1, Math.round(minutes))} min`
}

/** Walking time estimate for a straight-line distance at 4.8 km/h. Used only when no route exists. */
export function straightLineMinutes(metres: number): number {
  return metres / 80
}

export function formatMonth(month: string): string {
  const [year, m] = month.split('-').map(Number)
  if (!year || !m) return month
  return new Date(Date.UTC(year, m - 1, 1)).toLocaleDateString('en-GB', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  })
}

/** "2025-09..2026-08" becomes "September 2025 to August 2026". Other text is returned unchanged. */
export function formatPeriod(period: string): string {
  const parts = period.split('..')
  if (parts.length !== 2) return formatMonth(period)
  const [from, to] = parts
  return from === to ? formatMonth(from) : `${formatMonth(from)} to ${formatMonth(to)}`
}

/** Month of the police.uk records that the crime counts come from, for example "July 2026". */
export function crimeMonthLabel(crime: { month: string }): string {
  return formatMonth(crime.month)
}

export function formatPercent(share: number): string {
  return `${Math.round(share * 100)}%`
}

export function humanise(slug: string): string {
  const text = slug.replace(/[-_]+/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

// Display names for the platform's source ids; unknown ids are shown with `humanise`.
const SOURCE_NAMES: Record<string, string> = {
  bbc_london: 'BBC London',
  ea_floods: 'Environment Agency flood warnings',
  met_news: 'Metropolitan Police news',
  mylondon: 'MyLondon',
  standard_london: 'The Standard',
  tfl_road: 'TfL road disruptions',
  tfl_transit: 'TfL service status',
}

export function sourceName(id: string): string {
  return SOURCE_NAMES[id] ?? humanise(id)
}
