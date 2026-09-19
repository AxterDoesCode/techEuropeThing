import { useCallback, useEffect, useState } from 'react'

export interface LayerSettings {
  showCrime: boolean
  /** heatmap opacity, 0 to 1 */
  crimeOpacity: number
  /** events with a lower current risk are hidden */
  minRisk: number
  /** source ids switched off; stored instead of the enabled ones so a new source is shown by default */
  disabledSources: string[]
  layersCollapsed: boolean
  sidebarCollapsed: boolean
}

export const MIN_RISK_MAX = 0.5

export const DEFAULT_LAYER_SETTINGS: LayerSettings = {
  showCrime: true,
  crimeOpacity: 0.7,
  minRisk: 0,
  disabledSources: [],
  layersCollapsed: false,
  sidebarCollapsed: false,
}

const STORAGE_KEY = 'layerSettings'

const inRange = (v: unknown, min: number, max: number): v is number =>
  typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max

// Every field is checked separately; a missing or invalid field takes its default
function validate(raw: unknown): LayerSettings {
  const d = DEFAULT_LAYER_SETTINGS
  if (typeof raw !== 'object' || raw === null) return d
  const r = raw as Record<string, unknown>
  const bool = (v: unknown, fallback: boolean) => (typeof v === 'boolean' ? v : fallback)
  return {
    showCrime: bool(r.showCrime, d.showCrime),
    crimeOpacity: inRange(r.crimeOpacity, 0, 1) ? r.crimeOpacity : d.crimeOpacity,
    minRisk: inRange(r.minRisk, 0, MIN_RISK_MAX) ? r.minRisk : d.minRisk,
    disabledSources:
      Array.isArray(r.disabledSources) && r.disabledSources.every((s) => typeof s === 'string')
        ? (r.disabledSources as string[])
        : d.disabledSources,
    layersCollapsed: bool(r.layersCollapsed, d.layersCollapsed),
    sidebarCollapsed: bool(r.sidebarCollapsed, d.sidebarCollapsed),
  }
}

// In a window this short the expanded Layers panel moves the event feed below
// the visible part of the sidebar, so the panel starts collapsed
const SHORT_WINDOW_PX = 800

function load(): LayerSettings {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) return validate(JSON.parse(stored))
  } catch {
    // storage unavailable or not JSON: defaults
  }
  return { ...DEFAULT_LAYER_SETTINGS, layersCollapsed: window.innerHeight < SHORT_WINDOW_PX }
}

export type UpdateLayerSettings = (patch: Partial<LayerSettings>) => void

export function useLayerSettings(): [LayerSettings, UpdateLayerSettings] {
  const [settings, setSettings] = useState<LayerSettings>(load)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
    } catch {
      // not persisted
    }
  }, [settings])

  const update = useCallback<UpdateLayerSettings>((patch) => setSettings((s) => ({ ...s, ...patch })), [])
  return [settings, update]
}
