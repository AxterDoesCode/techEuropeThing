import type { RiskBlock } from '../api'
import { formatScore, percentileSentence, riskClass } from '../risk'

/** Labelled bar for the mean modelled risk within 300 m, with the London comparison sentence. */
export function RiskMeter({ risk }: { risk: RiskBlock | null }) {
  if (!risk) return <p className="muted">No modelled risk value for this location.</p>
  const cls = riskClass(risk.mean_score)
  return (
    <div className="risk-meter">
      <div className="risk-meter__head">
        <span className="risk-meter__label">Modelled risk within 300 m</span>
        <span className="risk-meter__value" style={{ background: cls.fill, color: cls.text }}>
          {formatScore(risk.mean_score)}
        </span>
      </div>
      <div
        className="risk-meter__track"
        role="meter"
        aria-label="Modelled risk within 300 m, scale 0 to 1"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={risk.mean_score}
        aria-valuetext={`${formatScore(risk.mean_score)} on a scale of 0 to 1`}
      >
        <div className="risk-meter__fill" style={{ width: `${Math.max(2, risk.mean_score * 100)}%`, background: cls.fill }} />
      </div>
      <div className="risk-meter__scale" aria-hidden="true">
        <span>0</span>
        <span>0.5</span>
        <span>1</span>
      </div>
      <p className="risk-meter__sentence">{percentileSentence(risk.london_percentile)}</p>
    </div>
  )
}
