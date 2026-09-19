import { useEffect, useState } from 'react'

export function Waiting({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000))
  return (
    <li className="message message-waiting">
      <span className="spinner" aria-hidden="true" />
      <span role="status">Looking up places and data…</span>
      <span className="waiting-timer" aria-hidden="true">
        {seconds} s
      </span>
      <span className="waiting-hint">An answer usually takes 5 to 25 seconds.</span>
    </li>
  )
}
