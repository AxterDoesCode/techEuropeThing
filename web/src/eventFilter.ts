import type { Category, EventFeature } from './types'

const SOURCE_LABEL: Record<string, string> = {
  tfl_road: 'TfL road disruptions',
  tfl_transit: 'TfL station disruptions',
  ea_floods: 'Environment Agency floods',
  met_news: 'Met Police news',
  bbc_london: 'BBC London news',
  manual: 'Manual reports',
}

// Unknown ids are shown by id
export const sourceLabel = (id: string) => SOURCE_LABEL[id] ?? id

// Events without source_ids (older exports) are grouped under this id
const NO_SOURCE = 'unknown'

export function sourceIdsOf(e: EventFeature): string[] {
  const ids = e.properties.source_ids
  return Array.isArray(ids) && ids.length > 0 ? ids : [NO_SOURCE]
}

// An event merged from several sources is shown while at least one of them is enabled
export function isEventShown(e: EventFeature, disabled: ReadonlySet<string>, minRisk: number): boolean {
  if ((e.properties.risk ?? 0) < minRisk) return false
  return sourceIdsOf(e).some((id) => !disabled.has(id))
}

export interface SourceInfo {
  id: string
  /** active events that list this source, before any filter */
  count: number
  /** most frequent category among those events: the marker colour seen most often for this source */
  category: Category | null
}

// Sources present in the events, plus disabled ones without events so they can be switched on again
export function listSources(events: EventFeature[], disabled: ReadonlySet<string>): SourceInfo[] {
  const byId = new Map<string, Map<Category, number>>()
  for (const e of events) {
    for (const id of sourceIdsOf(e)) {
      const cats = byId.get(id) ?? new Map<Category, number>()
      cats.set(e.properties.category, (cats.get(e.properties.category) ?? 0) + 1)
      byId.set(id, cats)
    }
  }
  for (const id of disabled) if (!byId.has(id)) byId.set(id, new Map())
  return [...byId.entries()]
    .map(([id, cats]) => {
      const ranked = [...cats.entries()].sort((a, b) => b[1] - a[1])
      return { id, count: ranked.reduce((sum, c) => sum + c[1], 0), category: ranked[0]?.[0] ?? null }
    })
    .sort((a, b) => b.count - a.count || a.id.localeCompare(b.id))
}
