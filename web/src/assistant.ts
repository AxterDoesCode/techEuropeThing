import { useCallback, useEffect, useRef, useState } from 'react'
import { ChatError, MAX_MESSAGE_CHARS, sendChat } from './chat'
import type { AssistantEvent, ChatArea, ChatMessage, ChatResponse, ChatUi, MapContext, RouteLabels } from './chat'
import type { RouteResult } from './types'

// Compact event cards open on the map at the same time
export const MAX_CARDS = 6

/** Everything the chatbot drew on the map. Replaced as a whole by each answer. */
export interface AssistantLayer {
  /** conversation entry whose `ui` is drawn; null = nothing drawn */
  entryId: number | null
  route: RouteResult | null
  routeLabels: RouteLabels | null
  area: ChatArea | null
  events: AssistantEvent[]
  highlightIds: string[]
  /** events shown as compact cards: at most MAX_CARDS, minus the ones the user closed */
  cardIds: string[]
  /** camera fit requested when the layer was applied */
  focus: 'route' | 'area' | null
  /** changes each time a layer is applied, so applying the same answer again moves the camera again */
  nonce: number
}

export const EMPTY_ASSISTANT_LAYER: AssistantLayer = {
  entryId: null, route: null, routeLabels: null, area: null, events: [], highlightIds: [], cardIds: [], focus: null, nonce: 0,
}

// Cards go to the highlighted events with the highest current risk. A response
// that highlights nothing gets cards for its highest-risk events instead.
function pickCardIds(ui: ChatUi): string[] {
  const highlighted = new Set(ui.highlightIds)
  const candidates = highlighted.size > 0 ? ui.events.filter((e) => highlighted.has(e.properties.id)) : ui.events
  return [...candidates]
    .sort((a, b) => b.properties.risk - a.properties.risk)
    .slice(0, MAX_CARDS)
    .map((e) => e.properties.id)
}

function layerFor(entryId: number, ui: ChatUi, fit: boolean): AssistantLayer {
  return {
    entryId,
    route: ui.route,
    routeLabels: ui.routeLabels,
    area: ui.area,
    events: ui.events,
    highlightIds: ui.highlightIds,
    cardIds: pickCardIds(ui),
    focus: fit ? ui.focus : null,
    nonce: Date.now(),
  }
}

export interface ChatEntry {
  id: number
  role: 'user' | 'assistant' | 'error'
  content: string
  /** assistant entries: the validated response */
  response?: ChatResponse
  /** user entries: the request failed or was cancelled; left out of later requests */
  failed?: boolean
}

export interface Assistant {
  layer: AssistantLayer
  entries: ChatEntry[]
  /** time the pending request started (ms since epoch); null = idle */
  pendingSince: number | null
  send: (text: string) => void
  cancel: () => void
  /** removes what the chatbot drew; the conversation stays */
  clearLayer: () => void
  /** clears the conversation and the layer, and cancels a pending request */
  newChat: () => void
  /** draws the `ui` of an earlier answer again; `fit` also moves the camera to it */
  showOnMap: (entryId: number, fit: boolean) => void
  closeCard: (eventId: string) => void
}

/**
 * Conversation with the chatbot and the map layer of its latest answer. They are
 * separate states: clearLayer keeps the conversation. `getContext` is read when a
 * question is sent.
 */
export function useAssistant(getContext: () => MapContext | null): Assistant {
  const [layer, setLayer] = useState<AssistantLayer>(EMPTY_ASSISTANT_LAYER)
  const [entries, setEntries] = useState<ChatEntry[]>([])
  const [pendingSince, setPendingSince] = useState<number | null>(null)
  const request = useRef<AbortController | null>(null)
  const nextId = useRef(1)
  const latest = useRef({ entries, getContext })
  useEffect(() => {
    latest.current = { entries, getContext }
  })
  useEffect(() => () => request.current?.abort(), [])

  const send = useCallback((text: string) => {
    const content = text.trim().slice(0, MAX_MESSAGE_CHARS)
    if (!content || request.current) return
    const userId = nextId.current++
    const conversation: ChatMessage[] = latest.current.entries
      .flatMap((e): ChatMessage[] => (e.role === 'error' || e.failed ? [] : [{ role: e.role, content: e.content }]))
      .concat({ role: 'user', content })
    const controller = new AbortController()
    request.current = controller
    setEntries((prev) => [...prev, { id: userId, role: 'user', content }])
    setPendingSince(Date.now())
    const finish = (added: ChatEntry, failed: boolean) => {
      // newChat or unmount replaced or dropped this request: its result is discarded
      if (request.current !== controller) return false
      request.current = null
      setPendingSince(null)
      setEntries((prev) => [...prev.map((e) => (e.id === userId && failed ? { ...e, failed } : e)), added])
      return true
    }
    sendChat(conversation, latest.current.getContext(), controller.signal).then(
      (response) => {
        const id = nextId.current++
        if (!finish({ id, role: 'assistant', content: response.answer, response }, false)) return
        // An answer replaces the previous layer, also when it draws nothing
        setLayer(response.ui ? layerFor(id, response.ui, true) : EMPTY_ASSISTANT_LAYER)
      },
      (e: unknown) => {
        const cancelled = e instanceof DOMException && e.name === 'AbortError'
        const message = cancelled ? 'Cancelled.' : e instanceof ChatError ? e.message : `The chat request failed: ${String(e)}`
        finish({ id: nextId.current++, role: 'error', content: message }, true)
      },
    )
  }, [])

  const cancel = useCallback(() => request.current?.abort(), [])
  const clearLayer = useCallback(() => setLayer(EMPTY_ASSISTANT_LAYER), [])
  const newChat = useCallback(() => {
    const pending = request.current
    request.current = null
    pending?.abort()
    setPendingSince(null)
    setEntries([])
    setLayer(EMPTY_ASSISTANT_LAYER)
  }, [])
  const showOnMap = useCallback((entryId: number, fit: boolean) => {
    const ui = latest.current.entries.find((e) => e.id === entryId)?.response?.ui
    if (ui) setLayer(layerFor(entryId, ui, fit))
  }, [])
  const closeCard = useCallback(
    (eventId: string) =>
      setLayer((l) => (l.cardIds.includes(eventId) ? { ...l, cardIds: l.cardIds.filter((id) => id !== eventId) } : l)),
    [],
  )

  return { layer, entries, pendingSince, send, cancel, clearLayer, newChat, showOnMap, closeCard }
}
