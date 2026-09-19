// Conversation items, their sessionStorage persistence and the derivation of
// the message list sent to the API.

import { RATE_LIMIT, RATE_WINDOW_MS } from './api'
import type { ChatMessage, ChatResponse } from './api'

export type Item =
  | { id: string; kind: 'user'; content: string }
  | { id: string; kind: 'assistant'; response: ChatResponse }
  // status: HTTP status, 0 for a network failure, -1 for a request stopped by the user.
  | { id: string; kind: 'error'; status: number; detail: string; retryAt: number | null }

export type AssistantItem = Extract<Item, { kind: 'assistant' }>

const ITEMS_KEY = 'ask.items.v1'
const SENDS_KEY = 'ask.sends.v1'

export function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

export function toMessages(items: Item[]): ChatMessage[] {
  const messages: ChatMessage[] = []
  for (const item of items) {
    if (item.kind === 'user') messages.push({ role: 'user', content: item.content })
    else if (item.kind === 'assistant') messages.push({ role: 'assistant', content: item.response.answer })
  }
  return messages
}

function isItem(value: unknown): value is Item {
  if (typeof value !== 'object' || value === null) return false
  const v = value as Record<string, unknown>
  if (typeof v.id !== 'string') return false
  if (v.kind === 'user') return typeof v.content === 'string'
  if (v.kind === 'error') return typeof v.status === 'number' && typeof v.detail === 'string'
  if (v.kind === 'assistant') {
    const r = v.response as Record<string, unknown> | null | undefined
    return (
      typeof r === 'object' &&
      r !== null &&
      typeof r.answer === 'string' &&
      Array.isArray(r.sources) &&
      Array.isArray(r.places) &&
      Array.isArray(r.tool_calls)
    )
  }
  return false
}

export function loadItems(): Item[] {
  try {
    const raw = sessionStorage.getItem(ITEMS_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(isItem) : []
  } catch {
    return []
  }
}

export function saveItems(items: Item[]): void {
  try {
    sessionStorage.setItem(ITEMS_KEY, JSON.stringify(items))
  } catch {
    // Storage can be unavailable or full; the conversation then lasts until reload.
  }
}

function loadSendTimes(now: number): number[] {
  try {
    const parsed: unknown = JSON.parse(sessionStorage.getItem(SENDS_KEY) ?? '[]')
    if (!Array.isArray(parsed)) return []
    return parsed.filter((t): t is number => typeof t === 'number' && now - t < RATE_WINDOW_MS)
  } catch {
    return []
  }
}

export function recordSend(now: number): void {
  try {
    sessionStorage.setItem(SENDS_KEY, JSON.stringify([...loadSendTimes(now), now]))
  } catch {
    // Ignored: the retry time estimate then falls back to the full window.
  }
}

// Time at which the rate limit admits a request again. The server counts per
// network address and sends no Retry-After header. When this tab sent all the
// counted questions, the oldest one leaves the window first; otherwise the
// full window from now is the latest possible time.
export function estimateRetryAt(now: number): number {
  const sends = loadSendTimes(now)
  if (sends.length >= RATE_LIMIT) return sends[sends.length - RATE_LIMIT] + RATE_WINDOW_MS
  return now + RATE_WINDOW_MS
}
