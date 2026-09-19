import { useEffect, useState } from 'react'

export function usePolling<T>(load: () => Promise<T>, intervalMs: number): { data: T | null; error: string | null } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const tick = () =>
      load()
        .then((d) => {
          if (cancelled) return
          setData(d)
          setError(null)
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof Error ? e.message : String(e))
        })
    tick()
    const timer = setInterval(tick, intervalMs)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [load, intervalMs])

  return { data, error }
}
