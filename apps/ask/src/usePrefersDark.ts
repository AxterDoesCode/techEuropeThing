import { useSyncExternalStore } from 'react'

const QUERY = '(prefers-color-scheme: dark)'

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(QUERY)
  media.addEventListener('change', onChange)
  return () => media.removeEventListener('change', onChange)
}

export function usePrefersDark(): boolean {
  return useSyncExternalStore(subscribe, () => window.matchMedia(QUERY).matches)
}
