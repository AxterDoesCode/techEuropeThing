import type { ChatResponse } from '../api'
import { isHttpUrl } from '../answerFormat'
import { describeToolCall, parseToolCalls } from '../toolCalls'

export function HowAnswered({ response }: { response: ChatResponse }) {
  const steps = parseToolCalls(response.tool_calls).map((call) => describeToolCall(call, response.places))
  const sources = response.sources.filter((s) => isHttpUrl(s.url))
  if (steps.length === 0 && sources.length === 0) return null
  return (
    <details className="how-answered">
      <summary>
        How this was answered
        <span className="how-count">
          {' '}
          ({steps.length} {steps.length === 1 ? 'step' : 'steps'}, {sources.length}{' '}
          {sources.length === 1 ? 'source' : 'sources'})
        </span>
      </summary>
      {steps.length > 0 && (
        <ol className="how-steps">
          {steps.map((step, i) => (
            <li key={i}>{step}</li>
          ))}
        </ol>
      )}
      <h3>Sources</h3>
      {sources.length > 0 ? (
        <ul className="how-sources">
          {sources.map((s) => (
            <li key={s.url}>
              <a href={s.url} target="_blank" rel="noopener noreferrer">
                {s.title}
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <p className="how-none">No event sources were linked to this answer. The figures come from the platform's data.</p>
      )}
    </details>
  )
}
