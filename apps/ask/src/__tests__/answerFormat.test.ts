import { describe, expect, it } from 'vitest'
import { parseAnswer, parseInline } from '../answerFormat'
import fixture from '../fixtures/chat-route.json'

describe('parseInline', () => {
  it('returns plain text unchanged', () => {
    expect(parseInline('a < b & c')).toEqual([{ type: 'text', text: 'a < b & c' }])
  })

  it('parses bold spans', () => {
    expect(parseInline('Score **0.41** here')).toEqual([
      { type: 'text', text: 'Score ' },
      { type: 'bold', text: '0.41' },
      { type: 'text', text: ' here' },
    ])
  })

  it('leaves unclosed bold markers as text', () => {
    expect(parseInline('a **b')).toEqual([{ type: 'text', text: 'a **b' }])
    expect(parseInline('****')).toEqual([{ type: 'text', text: '****' }])
  })

  it('links bare URLs and keeps trailing punctuation as text', () => {
    expect(parseInline('See https://example.org/a?b=1, then (https://example.org/x).')).toEqual([
      { type: 'text', text: 'See ' },
      { type: 'link', url: 'https://example.org/a?b=1' },
      { type: 'text', text: ', then (' },
      { type: 'link', url: 'https://example.org/x' },
      { type: 'text', text: ').' },
    ])
  })

  it('does not link other schemes or markup', () => {
    const text = 'javascript:alert(1) <a href="x">y</a> ftp://host/file'
    expect(parseInline(text)).toEqual([{ type: 'text', text }])
  })

  it('does not link an invalid URL', () => {
    expect(parseInline('https://')).toEqual([{ type: 'text', text: 'https://' }])
  })
})

describe('parseAnswer', () => {
  it('returns no blocks for empty input', () => {
    expect(parseAnswer('')).toEqual([])
    expect(parseAnswer(' \n\n ')).toEqual([])
  })

  it('splits paragraphs on blank lines and keeps line breaks inside one', () => {
    const blocks = parseAnswer('one\ntwo\n\nthree')
    expect(blocks).toEqual([
      { type: 'paragraph', lines: [[{ type: 'text', text: 'one' }], [{ type: 'text', text: 'two' }]] },
      { type: 'paragraph', lines: [[{ type: 'text', text: 'three' }]] },
    ])
  })

  it('groups "* " and "- " lines into one list between paragraphs', () => {
    const blocks = parseAnswer('Intro:\n* first\n- **second**\r\nAfter')
    expect(blocks.map((b) => b.type)).toEqual(['paragraph', 'list', 'paragraph'])
    expect(blocks[1]).toEqual({
      type: 'list',
      items: [[{ type: 'text', text: 'first' }], [{ type: 'bold', text: 'second' }]],
    })
  })

  it('accepts the U+2022 bullet character as a list marker', () => {
    const blocks = parseAnswer('\u2022 one\n\u2022 two')
    expect(blocks).toEqual([{ type: 'list', items: [[{ type: 'text', text: 'one' }], [{ type: 'text', text: 'two' }]] }])
  })

  it('does not treat a bold line or a bare marker as a list item', () => {
    expect(parseAnswer('**Note** text')[0].type).toBe('paragraph')
    expect(parseAnswer('*')[0].type).toBe('paragraph')
    expect(parseAnswer('-5 degrees')[0].type).toBe('paragraph')
  })

  it('parses the recorded answer as one list of four items', () => {
    const blocks = parseAnswer(fixture.answer)
    expect(blocks).toHaveLength(1)
    expect(blocks[0].type).toBe('list')
    expect(blocks[0].type === 'list' && blocks[0].items).toHaveLength(4)
  })
})
