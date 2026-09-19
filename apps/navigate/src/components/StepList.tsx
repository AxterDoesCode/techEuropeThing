import type { LngLat, RouteLeg, RouteStep } from '../api'
import { metres, minutes, risk } from '../format'

interface Props {
  leg: RouteLeg
  fromName: string
  toName: string
  onHighlight: (point: LngLat | null) => void
}

// A step is marked when its modelled risk is at least 0.3 and at least 1.25 times the route mean.
const isHigherRisk = (step: RouteStep, leg: RouteLeg): boolean => step.risk >= Math.max(0.3, leg.mean_risk * 1.25)

function Summary({ leg, fromName, toName }: Omit<Props, 'onHighlight'>) {
  return (
    <div className="trip-summary">
      <p><strong>{metres(leg.length_m)}</strong>, about <strong>{minutes(leg.duration_min)}</strong> on foot</p>
      <ol className="trip-ends">
        <li><span className="dot origin" aria-hidden="true">A</span>{fromName}</li>
        <li><span className="dot destination" aria-hidden="true">B</span>{toName}</li>
      </ol>
      <p className="fine">Turn-by-turn steps are not available from the routing service yet.</p>
    </div>
  )
}

export function StepList({ leg, fromName, toName, onHighlight }: Props) {
  const steps = leg.steps
  return (
    <section aria-label="Directions">
      <h2>Directions</h2>
      {!steps || steps.length === 0 ? (
        <Summary leg={leg} fromName={fromName} toName={toName} />
      ) : (
        <ol className="steps" onMouseLeave={() => onHighlight(null)}>
          {steps.map((step, i) => (
            <li
              key={i}
              className="step"
              tabIndex={0}
              onMouseEnter={() => onHighlight(step.start)}
              onFocus={() => onHighlight(step.start)}
              onBlur={() => onHighlight(null)}
            >
              <span className="step-index" aria-hidden="true">{i + 1}</span>
              <span className="step-body">
                <span className="step-instruction">{step.instruction}</span>
                <span className="step-meta">
                  {metres(step.distance_m)}
                  {step.lit === false && <span className="tag unlit" title="This segment has no recorded street lighting">Unlit</span>}
                  {isHigherRisk(step, leg) && (
                    <span className="tag higher" title={`Modelled risk ${risk(step.risk)}; route mean ${risk(leg.mean_risk)}`}>
                      Higher modelled risk {risk(step.risk)}
                    </span>
                  )}
                </span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
