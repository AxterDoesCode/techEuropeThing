import type { Item } from '../conversation'

type ErrorItem = Extract<Item, { kind: 'error' }>

function heading(status: number): string {
  if (status === -1) return 'Request stopped'
  if (status === 0) return 'Network error'
  if (status === 429) return 'Rate limit reached'
  if (status === 503) return 'Assistant not available'
  if (status === 502) return 'The assistant could not answer'
  return `Request failed (HTTP ${status})`
}

function clock(time: number): string {
  return new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function ErrorMessage({ item, onRetry }: { item: ErrorItem; onRetry: (() => void) | null }) {
  return (
    <li className="message message-error">
      <p>
        <strong>{heading(item.status)}.</strong> {item.detail}
      </p>
      {item.status === 429 && item.retryAt !== null && (
        <p>You can ask again at about {clock(item.retryAt)}; the limit counts questions over 10 minutes.</p>
      )}
      {onRetry && (
        <button type="button" className="retry" onClick={onRetry}>
          Retry
        </button>
      )}
    </li>
  )
}
