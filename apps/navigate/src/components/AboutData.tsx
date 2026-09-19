import { useEffect, useId, useRef, useState } from 'react'

export function AboutData() {
  const id = useId()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    rootRef.current?.scrollIntoView({ block: 'end' })
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && e.target instanceof Node && !rootRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown)
    }
  }, [open])

  return (
    <div className="about" ref={rootRef}>
      <button type="button" className="link-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}>
        About the data
      </button>
      <div id={id} className="about-panel" role="region" aria-label="About the data" hidden={!open}>
        <p>
          Modelled risk combines current events (transport disruption, incidents reported by news and official sources)
          with police-recorded crime for one recent month. Values run from 0 to 1 and are not probabilities.
        </p>
        <p>The crime data has no time of day, so it does not distinguish night from day.</p>
        <p>The lower-risk route reduces the modelled risk along the walk; it cannot account for events that are not in the data.</p>
        <p>Map and routing data © OpenStreetMap contributors (ODbL). Place search by Photon.</p>
      </div>
    </div>
  )
}
