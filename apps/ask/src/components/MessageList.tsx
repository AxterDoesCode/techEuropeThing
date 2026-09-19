import { useEffect, useRef } from 'react'
import type { Item } from '../conversation'
import { buildMapContext, isEmptyContext } from '../mapContext'
import { AssistantMessage } from './AssistantMessage'
import { ErrorMessage } from './ErrorMessage'
import { Suggestions } from './Suggestions'
import { Waiting } from './Waiting'

interface Props {
  items: Item[]
  selectedId: string | null
  waitingSince: number | null
  onSelect: (id: string) => void
  onRetry: () => void
  onSend: (text: string) => void
  onPrefill: (text: string) => void
}

export function MessageList({ items, selectedId, waitingSince, onSelect, onRetry, onSend, onPrefill }: Props) {
  const endRef = useRef<HTMLDivElement>(null)
  const waiting = waitingSince !== null

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' })
  }, [items.length, waiting])

  const lastIndex = items.length - 1
  return (
    <div className="messages-scroll">
      <ol className="messages" role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation">
        {items.map((item, index) => {
          const isLast = index === lastIndex && !waiting
          if (item.kind === 'user') {
            return (
              <li key={item.id} className="message message-user">
                <span className="visually-hidden">You: </span>
                {item.content}
              </li>
            )
          }
          if (item.kind === 'error') {
            return <ErrorMessage key={item.id} item={item} onRetry={isLast && item.status !== 429 ? onRetry : null} />
          }
          const hasMapContext = !isEmptyContext(buildMapContext(item.id, item.response))
          return (
            <AssistantMessage
              key={item.id}
              item={item}
              selected={item.id === selectedId}
              hasMapContext={hasMapContext}
              onSelect={() => onSelect(item.id)}
            >
              {isLast && (
                <Suggestions response={item.response} disabled={waiting} onSend={onSend} onPrefill={onPrefill} />
              )}
            </AssistantMessage>
          )
        })}
        {waitingSince !== null && <Waiting startedAt={waitingSince} />}
      </ol>
      <div ref={endRef} />
    </div>
  )
}
