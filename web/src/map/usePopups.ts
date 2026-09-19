import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { LngLat } from '../types'

export type PopupOffset = number | ((map: maplibregl.Map, at: LngLat) => number)

export interface PopupItem {
  /** identity of the popup: it is created when the key appears and removed when it disappears */
  key: string
  /** read when the popup is created; a popup does not move */
  at: LngLat
}

export interface PopupOptions {
  offset: PopupOffset
  className: string
  maxWidth: string
  /** the user closed the popup with its close button */
  onUserClose: (key: string) => void
}

interface OpenPopup {
  node: HTMLDivElement
  dispose: () => void
}

const NO_NODES: ReadonlyMap<string, HTMLDivElement> = new Map()

/**
 * MapLibre popups whose content is rendered by React through portals into the
 * returned nodes (key -> node). `items` is compared by key with the open popups:
 * new keys are opened, missing keys are removed, the others are left untouched.
 * `offset`, `className` and `maxWidth` are read when a popup is created.
 */
export function usePopups(
  mapRef: RefObject<maplibregl.Map | null>,
  items: readonly PopupItem[],
  options: PopupOptions,
): ReadonlyMap<string, HTMLDivElement> {
  const [nodes, setNodes] = useState(NO_NODES)
  const open = useRef(new Map<string, OpenPopup>())
  const latest = useRef({ items, options })
  useEffect(() => {
    latest.current = { items, options }
  })

  const keys = items.map((i) => i.key).join('\n')
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const popups = open.current
    const wanted = latest.current.items
    const wantedKeys = new Set(wanted.map((i) => i.key))
    let changed = false
    for (const [key, popup] of popups) {
      if (wantedKeys.has(key)) continue
      popup.dispose()
      popups.delete(key)
      changed = true
    }
    for (const item of wanted) {
      if (popups.has(item.key)) continue
      popups.set(item.key, openPopup(map, item, latest.current.options, (key) => latest.current.options.onUserClose(key)))
      changed = true
    }
    if (changed) setNodes(new Map([...popups].map(([key, p]) => [key, p.node])))
  }, [mapRef, keys])

  // Unmount (and the second effect run of React StrictMode): remove every popup
  useEffect(() => {
    const popups = open.current
    return () => {
      for (const popup of popups.values()) popup.dispose()
      popups.clear()
      setNodes(NO_NODES)
    }
  }, [])

  return nodes
}

function openPopup(map: maplibregl.Map, item: PopupItem, options: PopupOptions, onUserClose: (key: string) => void): OpenPopup {
  const { offset, className, maxWidth } = options
  const node = document.createElement('div')
  const offsetNow = () => (typeof offset === 'function' ? offset(map, item.at) : offset)
  // closeOnClick is off: the click that opens a popup would also close it
  const popup = new maplibregl.Popup({ offset: offsetNow(), maxWidth, closeOnClick: false, className })
    .setLngLat([item.at.lng, item.at.lat])
    .setDOMContent(node)
    .addTo(map)
  // remove() also fires 'close'; only a close made by the user is reported
  let disposed = false
  popup.on('close', () => {
    if (!disposed) onUserClose(item.key)
  })
  // MapLibre chooses the anchor side from the popup size when it positions the
  // popup. The content is rendered later through the portal, so position it
  // again when the size changes; otherwise a tall popup extends past the window edge.
  const resize = new ResizeObserver(() => popup.setLngLat(popup.getLngLat()))
  resize.observe(node)
  // An offset that depends on the zoom is recomputed while zooming
  const onZoom = () => popup.setOffset(offsetNow())
  if (typeof offset === 'function') map.on('zoom', onZoom)
  return {
    node,
    dispose: () => {
      disposed = true
      map.off('zoom', onZoom)
      resize.disconnect()
      popup.remove()
    },
  }
}

/** A single popup: exists while `key` is not null and is rebuilt when the key changes. */
export function usePopup(
  mapRef: RefObject<maplibregl.Map | null>,
  key: string | null,
  at: LngLat | null,
  options: PopupOptions,
): HTMLDivElement | null {
  const lng = at?.lng
  const lat = at?.lat
  const items = useMemo<PopupItem[]>(
    () => (key !== null && lng !== undefined && lat !== undefined ? [{ key, at: { lng, lat } }] : []),
    [key, lng, lat],
  )
  const nodes = usePopups(mapRef, items, options)
  return key === null ? null : (nodes.get(key) ?? null)
}
