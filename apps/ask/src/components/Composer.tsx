import { useEffect, useRef } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { MAX_MESSAGE_CHARS } from '../api'

interface Props {
  value: string
  waiting: boolean
  // Changes when the input is prefilled, so that focus moves to it.
  focusToken: number
  onChange: (value: string) => void
  onSubmit: () => void
  onStop: () => void
}

export function Composer({ value, waiting, focusToken, onChange, onSubmit, onStop }: Props) {
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const input = inputRef.current
    if (!input || focusToken === 0) return
    input.focus()
    input.setSelectionRange(input.value.length, input.value.length)
  }, [focusToken])

  useEffect(() => {
    if (!waiting && focusToken > 0) inputRef.current?.focus()
  }, [waiting, focusToken])

  const canSend = !waiting && value.trim() !== ''

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      if (canSend) onSubmit()
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (canSend) onSubmit()
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <label htmlFor="question" className="visually-hidden">
        Your question
      </label>
      <textarea
        id="question"
        ref={inputRef}
        rows={2}
        maxLength={MAX_MESSAGE_CHARS}
        placeholder="Ask about a place, a walk or hotels in London"
        value={value}
        disabled={waiting}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        aria-describedby="composer-hint"
      />
      {waiting ? (
        <button type="button" className="stop" onClick={onStop}>
          Stop
        </button>
      ) : (
        <button type="submit" className="send" disabled={!canSend}>
          Send
        </button>
      )}
      <p id="composer-hint" className="composer-hint">
        Enter sends, Shift+Enter adds a line.
      </p>
    </form>
  )
}
