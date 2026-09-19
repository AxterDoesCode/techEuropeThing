import { useState } from 'react'
import type { AlertLevel, OfficialAlert } from '../types'
import './alert-banner.css'

// Notice that an official wide-area alert (Met Office warning, UK Emergency Alert)
// is in force for London. The alerts are not drawn on the map and do not change
// risk scores or routes.

const LEVEL_RANK: Record<AlertLevel, number> = { yellow: 1, amber: 2, red: 3 }
const LEVEL_LABEL: Record<AlertLevel, string> = { yellow: 'Yellow', amber: 'Amber', red: 'Red' }
// Alerts shown before "+N more" is expanded
const COLLAPSED_COUNT = 2
const STORAGE_KEY = 'dismissedAlerts'
const TIME_ZONE = 'Europe/London'

// Alert id -> rank of the level it had when dismissed. Kept for the browser
// session. An alert is shown again when its level rises above the stored rank.
type Dismissed = Record<string, number>

function readDismissed(): Dismissed {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? '{}')
    return parsed !== null && typeof parsed === 'object' ? (parsed as Dismissed) : {}
  } catch {
    return {} // storage unavailable or invalid content
  }
}

function writeDismissed(value: Dismissed): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // not persisted: the dismissal lasts until the page is reloaded
  }
}

const dayKey = new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' })
const clock = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, hour: '2-digit', minute: '2-digit', hour12: false })
const weekday = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, weekday: 'short' })
const dayMonth = new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, weekday: 'short', day: 'numeric', month: 'short' })

/** "23:00 today", "08:00 tomorrow", "Thu 08:00", "Thu 1 Oct 08:00"; London time. */
function formatWhen(iso: string, now: Date): string {
  const t = new Date(iso)
  const time = clock.format(t)
  const key = dayKey.format(t)
  if (key === dayKey.format(now)) return `${time} today`
  if (key === dayKey.format(new Date(now.getTime() + 86_400_000))) return `${time} tomorrow`
  const days = Math.abs(t.getTime() - now.getTime()) / 86_400_000
  return days < 6 ? `${weekday.format(t)} ${time}` : `${dayMonth.format(t)} ${time}`
}

function validity(alert: OfficialAlert, now: Date): string {
  if (!alert.active) return `from ${formatWhen(alert.starts_at, now)}`
  return alert.ends_at ? `until ${formatWhen(alert.ends_at, now)}` : 'in force now'
}

function summary(alert: OfficialAlert): string {
  if (alert.source !== 'met_office') return alert.headline
  return `${alert.hazard.charAt(0).toUpperCase()}${alert.hazard.slice(1)} warning`
}

interface Props {
  alerts: OfficialAlert[] | null
  sidebarOpen: boolean
}

export function AlertBanner({ alerts, sidebarOpen }: Props) {
  const [dismissed, setDismissed] = useState<Dismissed>(readDismissed)
  const [expanded, setExpanded] = useState(false)

  const shown = (alerts ?? [])
    .filter((a) => (dismissed[a.id] ?? 0) < LEVEL_RANK[a.level])
    .sort((a, b) => LEVEL_RANK[b.level] - LEVEL_RANK[a.level] || a.starts_at.localeCompare(b.starts_at))
  if (shown.length === 0) return null

  const dismiss = (alert: OfficialAlert) => {
    const next = { ...readDismissed(), [alert.id]: LEVEL_RANK[alert.level] }
    writeDismissed(next)
    setDismissed(next)
  }
  const now = new Date()
  const visible = expanded ? shown : shown.slice(0, COLLAPSED_COUNT)
  const hiddenCount = shown.length - visible.length

  return (
    <div className={`alert-banner-area ${sidebarOpen ? 'with-sidebar' : ''}`}>
      <section className="alert-banner" aria-label="Official alerts for London">
        <ul>
          {visible.map((a) => (
            <li key={a.id} className={`level-${a.level}`}>
              <span className="level">{LEVEL_LABEL[a.level]}</span>
              <span className="summary" title={a.headline}>{summary(a)}</span>
              <span className="when">{validity(a, now)}</span>
              <span className="source">{a.source_label}</span>
              <a href={a.url} target="_blank" rel="noopener noreferrer">Details</a>
              <button type="button" className="dismiss" onClick={() => dismiss(a)} aria-label={`Dismiss: ${summary(a)}`}>
                ×
              </button>
            </li>
          ))}
        </ul>
        {(hiddenCount > 0 || expanded) && shown.length > COLLAPSED_COUNT && (
          <button type="button" className="more" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
            {expanded ? 'Show fewer' : `+${hiddenCount} more`}
          </button>
        )}
      </section>
    </div>
  )
}
