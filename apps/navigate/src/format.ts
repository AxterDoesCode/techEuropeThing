export const km = (m: number): string => `${(m / 1000).toFixed(1)} km`

export const metres = (m: number): string => (m >= 1000 ? km(m) : `${Math.round(m)} m`)

export const minutes = (min: number): string => `${Math.max(1, Math.round(min))} min`

export const risk = (value: number): string => value.toFixed(2)

export const percent = (share: number): string => `${Math.round(share * 100)}%`

const signed = (value: number, unit: string): string => `${value < 0 ? '−' : '+'}${Math.abs(value)} ${unit}`

export const signedMinutes = (min: number): string => signed(Math.round(min), 'min')

export const signedMetres = (m: number): string => signed(Math.round(m / 10) * 10, 'm')

// Local ISO 8601 time with UTC offset for 23:00 on the current local date.
export function tonightIso(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  const at = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 0, 0)
  const offsetMin = -at.getTimezoneOffset()
  const sign = offsetMin < 0 ? '-' : '+'
  const abs = Math.abs(offsetMin)
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T23:00:00` +
    `${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  )
}
