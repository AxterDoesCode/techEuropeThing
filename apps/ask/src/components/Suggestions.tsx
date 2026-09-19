import type { ChatResponse } from '../api'

interface Props {
  response: ChatResponse
  disabled: boolean
  onSend: (text: string) => void
  onPrefill: (text: string) => void
}

// Follow-ups are built from the first place the answer resolved; no model call.
export function Suggestions({ response, disabled, onSend, onPrefill }: Props) {
  const place = response.places.find((p) => p.found)
  if (!place) return null
  const name = place.query
  return (
    <div className="suggestions" role="group" aria-label="Follow-up questions">
      <button type="button" onClick={() => onPrefill(`Compare ${name} with `)}>
        Compare with a nearby area…
      </button>
      <button type="button" disabled={disabled} onClick={() => onSend(`Which hotels near ${name} have lower modelled risk?`)}>
        Hotels near {name}
      </button>
      <button type="button" onClick={() => onPrefill(`What should I know about walking from ${name} to `)}>
        Walking route from {name} to…
      </button>
    </div>
  )
}
