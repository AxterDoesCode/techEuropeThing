export function About() {
  return (
    <>
      <button type="button" className="header-button" popoverTarget="about-popover">
        About
      </button>
      <div id="about-popover" popover="auto" className="about" role="dialog" aria-label="About this assistant">
        <h2>About this assistant</h2>
        <ul>
          <li>
            Answers come from the platform's data: a modelled risk value (0 to 1) that combines current events with
            police-recorded crime for the period stated in each answer. It is not a probability.
          </li>
          <li>The crime data has no time of day, so it cannot describe night-time conditions separately.</li>
          <li>Recorded crime is higher where many people gather, such as stations and shopping streets.</li>
          <li>The assistant can be wrong. Check the linked sources.</li>
          <li>
            This is not an emergency service. <strong>Call 999 in an emergency.</strong>
          </li>
          <li>Map, hotel and station data © OpenStreetMap contributors. Basemap by OpenFreeMap.</li>
        </ul>
        <button type="button" popoverTarget="about-popover" popoverTargetAction="hide">
          Close
        </button>
      </div>
    </>
  )
}
