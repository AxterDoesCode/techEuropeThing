import type { ReactNode } from 'react'
import type { AssistantItem } from '../conversation'
import { AnswerText } from './AnswerText'
import { HowAnswered } from './HowAnswered'

interface Props {
  item: AssistantItem
  selected: boolean
  hasMapContext: boolean
  onSelect: () => void
  children?: ReactNode
}

export function AssistantMessage({ item, selected, hasMapContext, onSelect, children }: Props) {
  return (
    <li className={`message message-assistant${selected ? ' is-selected' : ''}`} onClick={hasMapContext ? onSelect : undefined}>
      <span className="visually-hidden">Assistant: </span>
      <AnswerText text={item.response.answer} />
      <HowAnswered response={item.response} />
      {hasMapContext && (
        <button type="button" className="show-on-map" aria-pressed={selected} onClick={hasMapContext ? onSelect : undefined}>
          {selected ? 'Shown on the map' : 'Show on the map'}
        </button>
      )}
      {children}
    </li>
  )
}
