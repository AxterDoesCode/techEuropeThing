import type { CrimePoints } from '../types'

interface Props {
  crime: CrimePoints | null
  showCrime: boolean
  onToggleCrime: (show: boolean) => void
}

function monthLabel(month: string): string {
  return new Date(`${month}-01T00:00:00Z`).toLocaleString('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' })
}

export function LayerPanel({ crime, showCrime, onToggleCrime }: Props) {
  const total = crime?.rows.reduce((sum, r) => sum + r[2], 0) ?? 0
  return (
    <section className="panel layers">
      <h2>Layers</h2>
      <label>
        <input type="checkbox" checked={showCrime} onChange={(e) => onToggleCrime(e.target.checked)} />
        <span>
          Met Police recorded crime
          <div className="meta">
            {crime
              ? `${monthLabel(crime.month)} · ${total.toLocaleString('en-GB')} crimes at ${crime.rows.length.toLocaleString('en-GB')} street points`
              : 'loading…'}
          </div>
        </span>
      </label>
      <div className="scale" />
      <div className="scale-labels"><span>low</span><span>risk / crime weight</span><span>high</span></div>
    </section>
  )
}
