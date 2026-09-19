import { useCallback, useEffect, useState } from 'react'
import { parseUrlState, serialiseUrlState, type UrlState } from '../urlState'

/**
 * State mirrored to the query string. `push` adds a history entry (new search);
 * without it the current entry is replaced (filters, sort, selection).
 */
export function useUrlState(): [UrlState, (patch: Partial<UrlState>, push?: boolean) => void] {
  const [state, setState] = useState<UrlState>(() => parseUrlState(window.location.search))

  useEffect(() => {
    const onPop = () => setState(parseUrlState(window.location.search))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const update = useCallback((patch: Partial<UrlState>, push = false) => {
    setState((current) => {
      const next = { ...current, ...patch }
      const url = `${window.location.pathname}${serialiseUrlState(next)}`
      if (push) window.history.pushState(null, '', url)
      else window.history.replaceState(null, '', url)
      return next
    })
  }, [])

  return [state, update]
}
