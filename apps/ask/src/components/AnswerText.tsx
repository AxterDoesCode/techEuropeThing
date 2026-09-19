import { useMemo } from 'react'
import { parseAnswer } from '../answerFormat'
import type { Inline } from '../answerFormat'

function Spans({ spans }: { spans: Inline[] }) {
  return (
    <>
      {spans.map((span, i) => {
        if (span.type === 'bold') return <strong key={i}>{span.text}</strong>
        if (span.type === 'link') {
          return (
            <a key={i} href={span.url} target="_blank" rel="noopener noreferrer">
              {span.url}
            </a>
          )
        }
        return <span key={i}>{span.text}</span>
      })}
    </>
  )
}

export function AnswerText({ text }: { text: string }) {
  const blocks = useMemo(() => parseAnswer(text), [text])
  return (
    <div className="answer-text">
      {blocks.map((block, i) =>
        block.type === 'list' ? (
          <ul key={i}>
            {block.items.map((item, j) => (
              <li key={j}>
                <Spans spans={item} />
              </li>
            ))}
          </ul>
        ) : (
          <p key={i}>
            {block.lines.map((line, j) => (
              <span key={j}>
                {j > 0 && <br />}
                <Spans spans={line} />
              </span>
            ))}
          </p>
        ),
      )}
    </div>
  )
}
