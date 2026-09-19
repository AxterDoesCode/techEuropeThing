import { describe, expect, it } from 'vitest'
import fixture from '../fixtures/chat-route.json'
import { buildMapContext } from '../mapContext'
import { describeToolCall, parseToolCall, parseToolCalls } from '../toolCalls'

describe('parseToolCall', () => {
  it('parses find_place with single quotes', () => {
    expect(parseToolCall("find_place('Brixton')")).toEqual({ tool: 'find_place', query: 'Brixton' })
  })

  it('parses find_place with double quotes around an apostrophe', () => {
    expect(parseToolCall('find_place("King\'s Cross")')).toEqual({ tool: 'find_place', query: "King's Cross" })
  })

  it('parses escaped quotes and keeps parentheses inside the query', () => {
    expect(parseToolCall("find_place('The \\'Old\\' Inn (Camden)')")).toEqual({
      tool: 'find_place',
      query: "The 'Old' Inn (Camden)",
    })
  })

  it('parses area_report and hotels_near', () => {
    expect(parseToolCall('area_report(51.4627, -0.1145, 500)')).toEqual({
      tool: 'area_report',
      lat: 51.4627,
      lng: -0.1145,
      radiusM: 500,
    })
    expect(parseToolCall('hotels_near(51.5154,-0.1755,1500)')).toEqual({
      tool: 'hotels_near',
      lat: 51.5154,
      lng: -0.1755,
      radiusM: 1500,
    })
  })

  it('parses walking_route', () => {
    expect(parseToolCall('walking_route((51.5324, -0.1230) -> (51.5100, -0.1303))')).toEqual({
      tool: 'walking_route',
      from: { lat: 51.5324, lng: -0.123 },
      to: { lat: 51.51, lng: -0.1303 },
    })
  })

  it.each([
    '',
    'find_place(',
    'find_place()',
    "find_place('')",
    'find_place(Brixton)',
    "find_place('Brixton\")",
    "find_place('a'b')",
    "find_place('trailing\\')",
    'area_report(51.46, -0.11)',
    'area_report(abc, -0.11, 500)',
    'area_report(51.46, -0.11, 500, 7)',
    'area_report(151.46, -0.11, 500)',
    'area_report(51.46, -0.11, 0)',
    'area_report(51.46, -0.11, -20)',
    'area_report(NaN, NaN, NaN)',
    'walking_route((51.53, -0.12) -> (51.51))',
    'walking_route((51.53, -0.12), (51.51, -0.13))',
    'walking_route((51.53, -0.12) -> (51.51, -200))',
    'delete_everything(1, 2, 3)',
    'area_report(51.46, -0.11, 500); drop',
    '<script>alert(1)</script>',
  ])('returns null for malformed or unknown input %j', (input) => {
    expect(parseToolCall(input)).toBeNull()
  })

  it.each([null, undefined, 42, {}, ['find_place']])('returns null for the non-string value %j', (input) => {
    expect(parseToolCall(input)).toBeNull()
  })
})

describe('parseToolCalls', () => {
  it('skips entries that do not parse and never throws', () => {
    expect(parseToolCalls(["find_place('Soho')", 'unknown()', 7, null])).toEqual([{ tool: 'find_place', query: 'Soho' }])
    expect(parseToolCalls('not an array')).toEqual([])
    expect(parseToolCalls(undefined)).toEqual([])
  })
})

describe('recorded response', () => {
  it('yields two places, two areas and one route', () => {
    const context = buildMapContext('x', fixture)
    expect(context.places).toHaveLength(2)
    expect(context.areas).toEqual([
      { lat: 51.51, lng: -0.1303, radiusM: 300 },
      { lat: 51.5324, lng: -0.123, radiusM: 300 },
    ])
    expect(context.routes).toHaveLength(1)
  })

  it('is described in plain language with place names', () => {
    const steps = parseToolCalls(fixture.tool_calls).map((c) => describeToolCall(c, fixture.places))
    expect(steps).toEqual([
      "Looked up “King's Cross”",
      'Looked up “Leicester Square”',
      "Compared walking routes from King's Cross to Leicester Square",
      'Read area data within 300 m of Leicester Square',
      "Read area data within 300 m of King's Cross",
    ])
  })

  it('marks a lookup without a matching place', () => {
    expect(describeToolCall({ tool: 'find_place', query: 'Nowhere' }, [])).toBe('Looked up “Nowhere” (no match found)')
  })
})
