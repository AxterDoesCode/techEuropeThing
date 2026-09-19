import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { searchPlaces } from '../search'
import type { PlaceResult } from '../search'
import './search.css'

interface Props {
  onSelect: (place: PlaceResult) => void
}

type Status = 'idle' | 'loading' | 'done' | 'error'

const DEBOUNCE_MS = 300
const MIN_QUERY_LENGTH = 3
const RECENT_KEY = 'recentPlaces'
const RECENT_LIMIT = 5

function isPlace(value: unknown): value is PlaceResult {
  if (typeof value !== 'object' || value === null) return false
  const p = value as Record<string, unknown>
  return (
    typeof p.id === 'string' && typeof p.label === 'string' && typeof p.detail === 'string' &&
    typeof p.kind === 'string' && typeof p.lng === 'number' && typeof p.lat === 'number' &&
    (p.bbox === null || (Array.isArray(p.bbox) && p.bbox.length === 4))
  )
}

function loadRecent(): PlaceResult[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]')
    return Array.isArray(parsed) ? parsed.filter(isPlace).slice(0, RECENT_LIMIT) : []
  } catch {
    return []
  }
}

function saveRecent(places: PlaceResult[]) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(places))
  } catch {
    // Storage can be unavailable or full; the list then lasts for this page load only.
  }
}

// Place search: geocodes the typed text and reports the chosen place. The
// list shows the recent selections while the input is focused and empty.
export function SearchBox({ onSelect }: Props) {
  const [text, setText] = useState('')
  const [results, setResults] = useState<PlaceResult[]>([])
  const [status, setStatus] = useState<Status>('idle')
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [recent, setRecent] = useState<PlaceResult[]>(loadRecent)

  const rootRef = useRef<HTMLElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const timerRef = useRef<number | undefined>(undefined)
  const abortRef = useRef<AbortController | null>(null)
  const listId = useId()

  const query = text.trim()
  const showRecent = query === ''
  const items = showRecent ? recent : results
  const optionId = (index: number) => `${listId}-option-${index}`

  function cancelPending() {
    window.clearTimeout(timerRef.current)
    abortRef.current?.abort()
    abortRef.current = null
  }

  useEffect(() => {
    function onPointerDown(e: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      window.clearTimeout(timerRef.current)
      abortRef.current?.abort()
    }
  }, [])

  async function run(q: string) {
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const found = await searchPlaces(q, controller.signal)
      if (controller.signal.aborted) return
      setResults(found)
      setStatus('done')
    } catch (e) {
      if (controller.signal.aborted) return
      setResults([])
      setError(e instanceof Error ? e.message : 'Place search failed')
      setStatus('error')
    }
  }

  function handleChange(value: string) {
    cancelPending()
    setText(value)
    setOpen(true)
    setActive(-1)
    setError(null)
    const q = value.trim()
    if (q.length < MIN_QUERY_LENGTH) {
      setResults([])
      setStatus('idle')
      return
    }
    setStatus('loading')
    timerRef.current = window.setTimeout(() => void run(q), DEBOUNCE_MS)
  }

  function select(place: PlaceResult) {
    cancelPending()
    setText(place.label)
    setResults([place])
    setStatus('done')
    setOpen(false)
    setActive(-1)
    const next = [place, ...recent.filter((r) => r.id !== place.id)].slice(0, RECENT_LIMIT)
    setRecent(next)
    saveRecent(next)
    onSelect(place)
  }

  function clear() {
    handleChange('')
    inputRef.current?.focus()
  }

  function moveActive(step: 1 | -1) {
    if (!open) {
      setOpen(true)
      return
    }
    if (items.length === 0) return
    const next = active < 0 ? (step === 1 ? 0 : items.length - 1) : (active + step + items.length) % items.length
    setActive(next)
    document.getElementById(optionId(next))?.scrollIntoView({ block: 'nearest' })
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      moveActive(e.key === 'ArrowDown' ? 1 : -1)
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const place = open ? items[active >= 0 ? active : 0] : undefined
      if (place) select(place)
    } else if (e.key === 'Escape') {
      setOpen(false)
      setActive(-1)
      inputRef.current?.blur()
    }
  }

  let message: string | null = null
  if (!showRecent) {
    if (status === 'error') message = error
    else if (query.length < MIN_QUERY_LENGTH) message = `Type at least ${MIN_QUERY_LENGTH} characters`
    else if (status === 'loading' && results.length === 0) message = 'Searching…'
    else if (status === 'done' && results.length === 0) message = 'No results in Greater London'
  }
  const listVisible = open && (items.length > 0 || message !== null)

  return (
    <section className="panel search" ref={rootRef}>
      <div className="search-field">
        <input
          ref={inputRef}
          type="text"
          role="combobox"
          aria-label="Search places"
          aria-autocomplete="list"
          aria-expanded={listVisible}
          aria-controls={listId}
          aria-activedescendant={listVisible && active >= 0 ? optionId(active) : undefined}
          aria-busy={status === 'loading'}
          autoComplete="off"
          spellCheck={false}
          placeholder="Search a street, area, station or postcode"
          value={text}
          onChange={(e) => handleChange(e.target.value)}
          onFocus={() => setOpen(true)}
          onKeyDown={handleKeyDown}
        />
        {status === 'loading' && <span className="search-spinner" aria-hidden="true" />}
        {text !== '' && (
          <button type="button" className="search-clear" aria-label="Clear search" onClick={clear}>
            ×
          </button>
        )}
      </div>
      <div className="search-dropdown" hidden={!listVisible}>
        {showRecent && items.length > 0 && <div className="search-heading">Recent</div>}
        <ul id={listId} role="listbox" aria-label={showRecent ? 'Recent places' : 'Search results'}>
          {items.map((place, i) => (
            <li
              key={place.id}
              id={optionId(i)}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'active' : undefined}
              // Keeps focus in the input so the click is not preceded by a blur.
              onMouseDown={(e) => e.preventDefault()}
              onMouseMove={() => setActive(i)}
              onClick={() => select(place)}
            >
              <span className="search-text">
                <span className="search-label">{place.label}</span>
                <span className="search-detail">{place.detail}</span>
              </span>
              <span className="search-kind">{place.kind}</span>
            </li>
          ))}
        </ul>
        {message !== null && (
          <p className={status === 'error' ? 'search-message error' : 'search-message'} role="status">
            {message}
          </p>
        )}
      </div>
    </section>
  )
}
