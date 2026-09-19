import { useEffect, useRef, useState } from 'react'

export function AboutData() {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // The detail panel also closes on Escape; the popover takes this key press.
      event.stopImmediatePropagation()
      setOpen(false)
      button.current?.focus()
    }
    const onPointer = (event: PointerEvent) => {
      if (root.current && event.target instanceof Node && !root.current.contains(event.target)) setOpen(false)
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('pointerdown', onPointer)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('pointerdown', onPointer)
    }
  }, [open])

  return (
    <div className="about" ref={root}>
      <button ref={button} type="button" className="button button--ghost" aria-expanded={open} aria-controls="about-data" onClick={() => setOpen(!open)}>
        About the data
      </button>
      {open && (
        <div id="about-data" className="about__popover" role="dialog" aria-label="About the data">
          <h2>About the data</h2>
          <ul>
            <li>
              Modelled risk is a value from 0 to 1 that combines current events with a baseline built from twelve
              months of Met Police recorded crime. The exact period is shown with the modelled risk in each hotel’s
              details. It is a modelled value, not a probability.
            </li>
            <li>
              “Higher than N% of London” is the share of scored London map cells with a lower value than the 300 m
              around the hotel.
            </li>
            <li>
              The crime counts shown around a hotel are police.uk records for one month, which is named next to the
              count. Neither crime data set has a time of day. Recorded crime is higher where many people gather
              (stations, nightlife, shopping streets).
            </li>
            <li>
              Current events come from news, police and transport sources. Often there is none within 300 m of a
              hotel.
            </li>
            <li>Hotels, stations and the map are © OpenStreetMap contributors. Hotel details can be incomplete.</li>
            <li>No prices or availability are provided. Use the hotel’s own website to book.</li>
            <li>Walking routes cover the area of the service’s walking network; outside it, only the straight-line distance to the station is shown.</li>
          </ul>
          <button type="button" className="button button--small" onClick={() => setOpen(false)}>
            Close
          </button>
        </div>
      )}
    </div>
  )
}
