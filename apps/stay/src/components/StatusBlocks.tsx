import type { ReactNode } from 'react'

export function Loading({ children }: { children: ReactNode }) {
  return (
    <p className="status status--loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {children}
    </p>
  )
}

export function ErrorNote({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="status status--error" role="alert">
      <p>{children}</p>
      {onRetry && (
        <button type="button" className="button button--small" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}
