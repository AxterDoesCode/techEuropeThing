import { describe, expect, it } from 'vitest'
import { ApiError, MAX_MESSAGES, normaliseChatResponse, normaliseRouteResponse, trimHistory } from '../api'
import type { ChatMessage } from '../api'
import routeFixture from '../fixtures/api-route.json'
import chatFixture from '../fixtures/chat-route.json'

describe('normaliseChatResponse', () => {
  it('reads the recorded response', () => {
    const response = normaliseChatResponse(chatFixture)
    expect(response.places).toHaveLength(2)
    expect(response.tool_calls).toHaveLength(5)
    expect(response.sources[0].url).toMatch(/^https:/)
  })

  it('ignores unknown top-level fields and malformed entries', () => {
    const response = normaliseChatResponse({
      answer: 'ok',
      ui: { cards: [1, 2] },
      sources: [{ url: 'https://example.org' }, 'x', { title: 'no url' }],
      places: [{ query: 'A', lat: 'x', lng: 0 }, null],
      tool_calls: ['find_place(\'A\')', 5, null],
    })
    expect(response).toEqual({
      answer: 'ok',
      sources: [{ title: 'https://example.org', url: 'https://example.org' }],
      places: [],
      tool_calls: ["find_place('A')"],
    })
  })

  it('accepts a response with only an answer', () => {
    expect(normaliseChatResponse({ answer: 'ok' })).toEqual({ answer: 'ok', sources: [], places: [], tool_calls: [] })
  })

  it('rejects a body without an answer', () => {
    expect(() => normaliseChatResponse({ sources: [] })).toThrow(ApiError)
    expect(() => normaliseChatResponse(null)).toThrow(ApiError)
  })
})

describe('normaliseRouteResponse', () => {
  it('reads the recorded response including the optional fields', () => {
    const route = normaliseRouteResponse(routeFixture)
    expect(route.safe.geometry.coordinates.length).toBeGreaterThan(2)
    expect(route.safe.lit_share).toBeGreaterThan(0)
    expect(route.safe.steps?.length).toBeGreaterThan(0)
    expect(route.night_multiplier).toBeGreaterThan(0)
  })

  it('accepts a response without the optional fields', () => {
    const leg = { geometry: { type: 'LineString', coordinates: [[0, 51], [0.1, 51.1]] }, length_m: 10 }
    const route = normaliseRouteResponse({ fast: leg, safe: leg })
    expect(route.safe.steps).toBeUndefined()
    expect(route.safe.lit_share).toBeUndefined()
    expect(route.night_multiplier).toBeUndefined()
    expect(Number.isNaN(route.safe.mean_risk)).toBe(true)
  })

  it('rejects a leg without geometry', () => {
    expect(() => normaliseRouteResponse({ fast: {}, safe: {} })).toThrow(ApiError)
  })
})

describe('trimHistory', () => {
  it('keeps the most recent messages and starts with a user message', () => {
    const messages: ChatMessage[] = []
    for (let i = 0; i < 41; i++) messages.push({ role: i % 2 === 0 ? 'user' : 'assistant', content: `m${i}` })
    const trimmed = trimHistory(messages)
    expect(trimmed.length).toBeLessThanOrEqual(MAX_MESSAGES)
    expect(trimmed[0].role).toBe('user')
    expect(trimmed[trimmed.length - 1].content).toBe('m40')
  })
})
