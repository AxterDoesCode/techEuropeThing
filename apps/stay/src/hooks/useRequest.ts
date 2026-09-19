import { useEffect, useState } from 'react'
import { ApiError } from '../api'

export type RequestState<T> =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: T }
  | { status: 'error'; error: ApiError }

function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  return new ApiError(0, err instanceof Error ? err.message : 'Unexpected error')
}

/**
 * Runs `load` whenever `key` changes. The previous request is aborted, and a response
 * that arrives after its key was replaced is discarded. A null key means no request.
 * `retryToken` re-runs the request for the same key.
 */
export function useRequest<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  retryToken = 0,
): RequestState<T> {
  const [result, setResult] = useState<{ key: string; token: number; state: RequestState<T> } | null>(null)

  useEffect(() => {
    if (key === null) return
    const controller = new AbortController()
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ key, token: retryToken, state: { status: 'success', data } })
      },
      (err: unknown) => {
        if (controller.signal.aborted) return
        setResult({ key, token: retryToken, state: { status: 'error', error: toApiError(err) } })
      },
    )
    return () => controller.abort()
    // `load` is a new closure on every render; `key` identifies the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, retryToken])

  if (key === null) return { status: 'idle' }
  if (result && result.key === key && result.token === retryToken) return result.state
  return { status: 'loading' }
}
