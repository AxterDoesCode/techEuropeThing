import { QUICK_PICKS } from '../places'
import type { SearchPlace } from '../urlState'

export function QuickPicks({ onPick }: { onPick: (place: SearchPlace) => void }) {
  return (
    <div className="quick-picks" role="group" aria-label="Popular areas">
      {QUICK_PICKS.map((pick) => (
        <button key={pick.label} type="button" className="chip" onClick={() => onPick(pick)}>
          {pick.label}
        </button>
      ))}
    </div>
  )
}
