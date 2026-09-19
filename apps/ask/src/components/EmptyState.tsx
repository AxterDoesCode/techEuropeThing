const EXAMPLES = [
  "What's happening around Brixton station?",
  'What does the data show for the area around Camden Town?',
  "I'm walking from King's Cross to Leicester Square tonight, what should I know?",
  'Compare the walking routes from Waterloo station to Covent Garden.',
  'Which hotels near Paddington have lower modelled risk?',
  'Which hotels near Liverpool Street station have lower modelled risk around them?',
]

export function EmptyState({ onAsk, disabled }: { onAsk: (text: string) => void; disabled: boolean }) {
  return (
    <div className="empty-state">
      <h2>Ask about an area of London</h2>
      <p>
        Answers report modelled risk, police-recorded crime and current events for a place, compare walking routes, or
        list hotels with the modelled risk around them.
      </p>
      <ul className="examples">
        {EXAMPLES.map((text) => (
          <li key={text}>
            <button type="button" className="example" disabled={disabled} onClick={() => onAsk(text)}>
              {text}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
