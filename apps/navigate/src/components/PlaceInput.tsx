import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { MIN_QUERY_LENGTH, searchPlaces, type Place } from '../geocode'

const DEBOUNCE_MS = 300

interface Props {
  label: string
  placeholder: string
  // Name of the point that is set, or '' when none is.
  value: string
  active: boolean
  onFocus: () => void
  onSelect: (place: Place) => void
  onClear: () => void
}

type Search =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; places: Place[] }
  | { status: 'error'; message: string }

// Text input with a Photon suggestion list (ARIA combobox pattern).
export function PlaceInput({ label, placeholder, value, active, onFocus, onSelect, onClear }: Props) {
  const id = useId()
  const [text, setText] = useState(value)
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState<Search>({ status: 'idle' })
  const [cursor, setCursor] = useState(0)
  // False until the user types, so a value set from outside does not start a search.
  const typedRef = useRef(false)

  useEffect(() => {
    typedRef.current = false
    setText(value)
    setSearch({ status: 'idle' })
  }, [value])

  useEffect(() => {
    if (!typedRef.current) return
    const query = text.trim()
    if (query.length < MIN_QUERY_LENGTH) {
      setSearch({ status: 'idle' })
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setSearch({ status: 'loading' })
      searchPlaces(query, controller.signal)
        .then((places) => {
          setSearch({ status: 'ready', places })
          setCursor(0)
        })
        .catch((e: unknown) => {
          if (controller.signal.aborted) return
          setSearch({ status: 'error', message: e instanceof Error ? e.message : String(e) })
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [text])

  const places = search.status === 'ready' ? search.places : []
  const listOpen = open && search.status !== 'idle'

  const choose = (place: Place) => {
    typedRef.current = false
    setOpen(false)
    setSearch({ status: 'idle' })
    setText(place.name)
    onSelect(place)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (places.length === 0) return
      e.preventDefault()
      setOpen(true)
      setCursor((c) => (c + (e.key === 'ArrowDown' ? 1 : places.length - 1)) % places.length)
    } else if (e.key === 'Enter') {
      if (listOpen && places[cursor]) {
        e.preventDefault()
        choose(places[cursor])
      }
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className={active ? 'place-input active' : 'place-input'}>
      <label htmlFor={id}>{label}</label>
      <div className="place-field">
        <input
          id={id}
          type="text"
          role="combobox"
          autoComplete="off"
          spellCheck={false}
          placeholder={placeholder}
          value={text}
          aria-expanded={listOpen}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          aria-activedescendant={listOpen && places[cursor] ? `${id}-option-${cursor}` : undefined}
          onFocus={(e) => {
            onFocus()
            setOpen(true)
            e.target.select()
          }}
          onBlur={() => {
            setOpen(false)
            // Unfinished typing is discarded in favour of the point that is set.
            if (typedRef.current && text.trim() !== '') {
              typedRef.current = false
              setText(value)
            }
          }}
          onChange={(e) => {
            typedRef.current = true
            setText(e.target.value)
            setOpen(true)
            if (e.target.value === '' && value !== '') onClear()
          }}
          onKeyDown={onKeyDown}
        />
        {text !== '' && (
          <button
            type="button"
            className="icon-button clear"
            aria-label={`Clear ${label}`}
            onClick={() => {
              typedRef.current = false
              setText('')
              setSearch({ status: 'idle' })
              onClear()
            }}
          >
            ×
          </button>
        )}
      </div>
      <ul id={`${id}-list`} role="listbox" aria-label={`${label} suggestions`} className="suggestions" hidden={!listOpen}>
        {search.status === 'loading' && <li className="suggestion-note" role="presentation">Searching…</li>}
        {search.status === 'error' && <li className="suggestion-note error-text" role="presentation">{search.message}</li>}
        {search.status === 'ready' && places.length === 0 && (
          <li className="suggestion-note" role="presentation">No places found in London</li>
        )}
        {places.map((place, i) => (
          <li
            key={place.id}
            id={`${id}-option-${i}`}
            role="option"
            aria-selected={i === cursor}
            className={i === cursor ? 'suggestion current' : 'suggestion'}
            // mousedown precedes the input's blur; preventDefault keeps focus so the click is handled.
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => choose(place)}
            onMouseEnter={() => setCursor(i)}
          >
            <span className="suggestion-name">{place.name}</span>
            <span className="suggestion-detail">{place.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
