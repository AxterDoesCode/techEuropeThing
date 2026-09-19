import { useEffect, useId, useState } from 'react'

interface Props {
  alpha: number
  night: boolean
  onAlpha: (alpha: number) => void
  onNight: (night: boolean) => void
}

export function Preferences({ alpha, night, onAlpha, onNight }: Props) {
  const id = useId()
  // Value while the slider is being moved. It is committed on release, which starts one route request.
  const [draft, setDraft] = useState(alpha)
  useEffect(() => setDraft(alpha), [alpha])
  const commit = () => {
    if (draft !== alpha) onAlpha(draft)
  }

  return (
    <section className="preferences" aria-label="Route preferences">
      <div className="slider">
        <label htmlFor={`${id}-alpha`}>
          Safety preference <output htmlFor={`${id}-alpha`}>{draft}</output>
        </label>
        <input
          id={`${id}-alpha`}
          type="range"
          min={0}
          max={10}
          step={1}
          value={draft}
          aria-valuetext={`${draft} of 10`}
          onChange={(e) => setDraft(Number(e.target.value))}
          onPointerUp={commit}
          onKeyUp={commit}
          onBlur={commit}
        />
        <div className="slider-ends" aria-hidden="true"><span>Shortest</span><span>Avoid risk more</span></div>
      </div>
      <label className="toggle">
        <input type="checkbox" role="switch" checked={night} onChange={(e) => onNight(e.target.checked)} />
        <span className="toggle-track" aria-hidden="true" />
        <span>Walking at night</span>
      </label>
    </section>
  )
}
