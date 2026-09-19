import { useCallback, useEffect, useRef, useState } from 'react'
import { parseUrlState, serialiseUrlState, type UrlState } from '../urlState'

/**
 * State mirrored to the query string. `push` adds a history entry (new search);
 * without it the current entry is replaced (filters, sort, selection).
 */
export function useUrlState(): [UrlState, (patch: Partial<UrlState>, push?: boolean) => void] {
  const [state, setState] = useState<UrlState>(() => parseUrlState(window.location.search))

  // Latest state, read by `update` so the history call happens outside a state updater
  // (React runs updaters twice in StrictMode, which would push two history entries).
  const latest = useRef(state)

  useEffect(() => {
    const onPop = () => {
      latest.current = parseUrlState(window.location.search)
      setState(latest.current)
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const update = useCallback((patch: Partial<UrlState>, push = false) => {
    const next = { ...latest.current, ...patch }
    latest.current = next
    const url = `${window.location.pathname}${serialiseUrlState(next)}`
    if (push) window.history.pushState(null, '', url)
    else window.history.replaceState(null, '', url)
    setState(next)
  }, [])

  return [state, update]
}
