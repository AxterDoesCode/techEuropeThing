import { useEffect, useId, useRef, useState } from 'react'
import { searchPlaces, type PlaceSuggestion } from '../photon'
import { RADIUS_OPTIONS, type SearchPlace } from '../urlState'

const DEBOUNCE_MS = 300
const MIN_CHARS = 3

type SuggestState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'done'; items: PlaceSuggestion[] }
  | { status: 'error' }

interface Props {
  place: SearchPlace | null
  radius: number
  compact?: boolean
  onSearch: (place: SearchPlace) => void
  onRadiusChange: (radius: number) => void
}

export function SearchBar({ place, radius, compact = false, onSearch, onRadiusChange }: Props) {
  const [text, setText] = useState(place?.label ?? '')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [suggest, setSuggest] = useState<SuggestState>({ status: 'idle' })
  const listId = useId()
  const inputId = useId()
  const radiusId = useId()
  const typed = useRef(false)

  // The input follows the searched place when it changes from outside (quick pick, back button).
  const [lastLabel, setLastLabel] = useState(place?.label ?? '')
  if ((place?.label ?? '') !== lastLabel) {
    setLastLabel(place?.label ?? '')
    setText(place?.label ?? '')
  }

  useEffect(() => {
    const query = text.trim()
    if (!typed.current || query.length < MIN_CHARS) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      setSuggest({ status: 'loading' })
      searchPlaces(query, controller.signal).then(
        (items) => {
          if (controller.signal.aborted) return
          setSuggest({ status: 'done', items })
          setActive(-1)
        },
        () => {
          if (!controller.signal.aborted) setSuggest({ status: 'error' })
        },
      )
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [text])

  const items = suggest.status === 'done' ? suggest.items : []
  const tooShort = text.trim().length < MIN_CHARS
  const showList = open && !tooShort && suggest.status !== 'idle'

  function choose(item: PlaceSuggestion) {
    typed.current = false
    setText(item.name)
    setOpen(false)
    onSearch({ label: item.name, lng: item.center[0], lat: item.center[1] })
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (items.length === 0) return
      setOpen(true)
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((i) => (i + step + items.length) % items.length)
    } else if (event.key === 'Escape') {
      setOpen(false)
    }
  }

  function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    const item = items[active >= 0 ? active : 0]
    if (showList && item) choose(item)
  }

  return (
    <form className={compact ? 'search search--compact' : 'search'} role="search" onSubmit={onSubmit}>
      <div className="search__field">
        <label className="search__label" htmlFor={inputId}>
          Where in London?
        </label>
        <input
          id={inputId}
          className="search__input"
          type="text"
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 && showList ? `${listId}-${active}` : undefined}
          autoComplete="off"
          placeholder="Area, station, street or postcode"
          value={text}
          onChange={(e) => {
            typed.current = true
            setText(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKeyDown}
        />
        <ul id={listId} className="search__list" role="listbox" aria-label="Place suggestions" hidden={!showList}>
          {suggest.status === 'loading' && <li className="search__note">Searching places…</li>}
          {suggest.status === 'error' && <li className="search__note">Place search is unavailable. Use a quick pick.</li>}
          {suggest.status === 'done' && items.length === 0 && (
            <li className="search__note">No place in London matches “{text.trim()}”.</li>
          )}
          {items.map((item, i) => (
            <li
              key={item.key}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={i === active ? 'search__option is-active' : 'search__option'}
              // mousedown fires before the input's blur closes the list
              onMouseDown={(e) => {
                e.preventDefault()
                choose(item)
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="search__option-name">{item.name}</span>
              {item.context && <span className="search__option-context">{item.context}</span>}
            </li>
          ))}
        </ul>
      </div>
      <div className="search__radius">
        <label className="search__label" htmlFor={radiusId}>
          Within
        </label>
        <select id={radiusId} value={radius} onChange={(e) => onRadiusChange(Number(e.target.value))}>
          {RADIUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <button className="button button--primary search__submit" type="submit">
        Search
      </button>
    </form>
  )
}
