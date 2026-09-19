// Parses the plain-text answer into blocks and inline spans. Supported:
// paragraphs separated by blank lines, list items on lines starting with
// "* ", "- " or "\u2022 ", **bold**, and bare http(s) URLs. Everything else stays text;
// React escapes it when rendering.

export type Inline =
  | { type: 'text'; text: string }
  | { type: 'bold'; text: string }
  | { type: 'link'; url: string }

export type Block = { type: 'paragraph'; lines: Inline[][] } | { type: 'list'; items: Inline[][] }

const INLINE_RE = /\*\*([^*\n]+?)\*\*|(https?:\/\/[^\s<>"'`]+)/g
// The model also writes list items with the U+2022 bullet character.
const LIST_ITEM_RE = /^\s*[*\-\u2022]\s+(\S.*)$/
// Punctuation at the end of a URL usually belongs to the sentence.
const TRAILING_PUNCTUATION_RE = /[.,;:!?)\]]+$/

function pushText(out: Inline[], text: string): void {
  if (text === '') return
  const last = out[out.length - 1]
  if (last && last.type === 'text') last.text += text
  else out.push({ type: 'text', text })
}

export function parseInline(text: string): Inline[] {
  const out: Inline[] = []
  let index = 0
  for (const match of text.matchAll(INLINE_RE)) {
    const start = match.index
    pushText(out, text.slice(index, start))
    if (match[1] !== undefined) {
      out.push({ type: 'bold', text: match[1] })
    } else {
      const raw = match[2]
      const url = raw.replace(TRAILING_PUNCTUATION_RE, '')
      if (isHttpUrl(url)) {
        out.push({ type: 'link', url })
        pushText(out, raw.slice(url.length))
      } else {
        pushText(out, raw)
      }
    }
    index = start + match[0].length
  }
  pushText(out, text.slice(index))
  return out
}

export function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

export function parseAnswer(answer: string): Block[] {
  const blocks: Block[] = []
  let current: Block | null = null
  for (const rawLine of answer.replace(/\r\n?/g, '\n').split('\n')) {
    const line = rawLine.trimEnd()
    if (line.trim() === '') {
      current = null
      continue
    }
    const item = LIST_ITEM_RE.exec(line)
    if (item) {
      if (!current || current.type !== 'list') {
        current = { type: 'list', items: [] }
        blocks.push(current)
      }
      current.items.push(parseInline(item[1]))
    } else {
      if (!current || current.type !== 'paragraph') {
        current = { type: 'paragraph', lines: [] }
        blocks.push(current)
      }
      current.lines.push(parseInline(line.trim()))
    }
  }
  return blocks
}
