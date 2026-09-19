import { useEffect, useState } from 'react'
import { fetchRoute, isAbort, type LngLat, type RouteResponse } from './api'
import { tonightIso } from './format'

export type RouteState =
  | { status: 'idle' }
  | { status: 'loading'; previous: RouteResponse | null }
  | { status: 'ready'; data: RouteResponse }
  | { status: 'error'; message: string; outsideArea: boolean }

// One POST /api/route per change of inputs. The request of the previous inputs is aborted.
export function useRoute(
  origin: LngLat | null,
  destination: LngLat | null,
  alpha: number,
  night: boolean,
  attempt: number,
): RouteState {
  const [state, setState] = useState<RouteState>({ status: 'idle' })
  const [oLng, oLat] = origin ?? [null, null]
  const [dLng, dLat] = destination ?? [null, null]

  useEffect(() => {
    if (oLng === null || oLat === null || dLng === null || dLat === null) {
      setState({ status: 'idle' })
      return
    }
    const controller = new AbortController()
    setState((s) => ({ status: 'loading', previous: s.status === 'ready' ? s.data : s.status === 'loading' ? s.previous : null }))
    fetchRoute(
      { origin: [oLng, oLat], destination: [dLng, dLat], alpha, ...(night ? { depart_at: tonightIso() } : {}) },
      controller.signal,
    )
      .then((data) => setState({ status: 'ready', data }))
      .catch((e: unknown) => {
        if (isAbort(e) || controller.signal.aborted) return
        const status = typeof e === 'object' && e !== null && 'status' in e ? (e as { status: unknown }).status : null
        setState({
          status: 'error',
          message: e instanceof Error ? e.message : String(e),
          outsideArea: status === 422,
        })
      })
    return () => controller.abort()
  }, [oLng, oLat, dLng, dLat, alpha, night, attempt])

  return state
}
