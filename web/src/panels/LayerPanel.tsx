import { CATEGORY_COLOR, CATEGORY_LABEL } from '../map/colors'
import { monthLabel } from '../map/crimeIndex'
import { sourceLabel, type SourceInfo } from '../eventFilter'
import { MIN_RISK_MAX, type LayerSettings, type UpdateLayerSettings } from '../layerSettings'
import type { CrimePoints } from '../types'

interface Props {
  crime: CrimePoints | null
  settings: LayerSettings
  onChange: UpdateLayerSettings
  sources: SourceInfo[]
  /** events before and after the source and minimum risk filters */
  totalEvents: number
  shownEvents: number
}

const NO_CATEGORY_COLOR = 'rgb(150, 150, 150)'

export function LayerPanel({ crime, settings, onChange, sources, totalEvents, shownEvents }: Props) {
  const total = crime?.rows.reduce((sum, r) => sum + r[2], 0) ?? 0
  const disabled = new Set(settings.disabledSources)
  const hidden = totalEvents - shownEvents
  const collapsed = settings.layersCollapsed

  const toggleSource = (id: string, on: boolean) =>
    onChange({ disabledSources: on ? settings.disabledSources.filter((s) => s !== id) : [...settings.disabledSources, id] })

  return (
    <section className={`panel layers ${collapsed ? 'collapsed' : ''}`}>
      <h2>
        <button
          type="button" className="collapse" aria-expanded={!collapsed}
          onClick={() => onChange({ layersCollapsed: !collapsed })}
        >
          <span>Layers and filters</span>
          <span className="state">
            {hidden > 0 ? `${hidden} hidden · ` : ''}{collapsed ? 'show' : 'hide'}
          </span>
        </button>
      </h2>
      {!collapsed && (
        <>
          <label className="check">
            <input
              type="checkbox" checked={settings.showCrime}
              onChange={(e) => onChange({ showCrime: e.target.checked })}
            />
            <span>
              Met Police recorded crime
              <div className="meta">
                {crime
                  ? `${monthLabel(crime.month)} · ${total.toLocaleString('en-GB')} crimes at ${crime.rows.length.toLocaleString('en-GB')} street points`
                  : 'loading…'}
              </div>
            </span>
          </label>
          <div className="scale heat" />
          <div className="scale-labels"><span>elevated</span><span>crime density</span><span>highest</span></div>
          <p className="meta">
            Density of recorded crime, weighted by relevance to personal safety; low density is not drawn. Click the
            map for details of the crimes within 150 m.
          </p>
          <label className="slider">
            <span>Heatmap opacity <code>{Math.round(settings.crimeOpacity * 100)}%</code></span>
            <input
              type="range" min={0} max={100} step={5} value={Math.round(settings.crimeOpacity * 100)}
              disabled={!settings.showCrime} aria-label="Heatmap opacity"
              onChange={(e) => onChange({ crimeOpacity: Number(e.target.value) / 100 })}
            />
          </label>

          <h3>
            Sources
            <span className="shortcuts">
              <button type="button" onClick={() => onChange({ disabledSources: [] })}>all</button>
              <button type="button" onClick={() => onChange({ disabledSources: sources.map((s) => s.id) })}>none</button>
            </span>
          </h3>
          {sources.length === 0 && <p className="meta">No events loaded.</p>}
          <ul className="sources">
            {sources.map((s) => (
              <li key={s.id}>
                <label className="check">
                  <input
                    type="checkbox" checked={!disabled.has(s.id)} data-source={s.id}
                    onChange={(e) => toggleSource(s.id, e.target.checked)}
                  />
                  <span
                    className="dot"
                    title={s.category ? `Marker colour of its most frequent category: ${CATEGORY_LABEL[s.category]}` : undefined}
                    style={{ background: s.category ? `rgb(${CATEGORY_COLOR[s.category].join(',')})` : NO_CATEGORY_COLOR }}
                  />
                  <span className="name">{sourceLabel(s.id)}</span>
                  <span className="count">{s.count}</span>
                </label>
              </li>
            ))}
          </ul>

          <label className="slider">
            <span>Minimum risk <code>{settings.minRisk.toFixed(2)}</code></span>
            <input
              type="range" min={0} max={MIN_RISK_MAX} step={0.05} value={settings.minRisk} aria-label="Minimum risk"
              onChange={(e) => onChange({ minRisk: Number(e.target.value) })}
            />
          </label>
          <p className="meta hidden-count">
            {hidden > 0 ? `${hidden} of ${totalEvents} events hidden` : `All ${totalEvents} events shown`}
          </p>

          <div className="scale" />
          <div className="scale-labels"><span>low</span><span>event risk (areas and lines)</span><span>high</span></div>
        </>
      )}
    </section>
  )
}
