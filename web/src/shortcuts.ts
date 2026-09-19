import { useEffect } from 'react'

// A single-letter shortcut applies only to a plain key press: not while text is
// being entered, a modifier key is held, or the list of the search box is open
function isPlainKeyPress(e: KeyboardEvent, key: string): boolean {
  if (e.key.toLowerCase() !== key || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return false
  if (e.isComposing || e.repeat || e.defaultPrevented) return false
  const el = e.target instanceof HTMLElement ? e.target : null
  if (el && (el.isContentEditable || el.closest('input, textarea, select'))) return false
  return !document.querySelector('[role="combobox"][aria-expanded="true"]')
}

/** Calls `action` when the lower-case letter `key` is pressed as a plain key press. */
export function useLetterShortcut(key: string, action: () => void): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isPlainKeyPress(e, key)) action()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [key, action])
}
