import type { SearchPlace } from '../urlState'
import { QuickPicks } from './QuickPicks'
import { SearchBar } from './SearchBar'

interface Props {
  radius: number
  onSearch: (place: SearchPlace) => void
  onRadiusChange: (radius: number) => void
}

export function Landing({ radius, onSearch, onRadiusChange }: Props) {
  return (
    <main className="landing">
      <div className="landing__inner">
        <h1>Find a place to stay in London, ranked by modelled risk</h1>
        <p className="landing__lead">
          Hotels, hostels and guest houses from OpenStreetMap, ordered by the modelled risk of the 300 m around them,
          with recorded crime, current events and the walk from the nearest station.
        </p>
        <SearchBar place={null} radius={radius} onSearch={onSearch} onRadiusChange={onRadiusChange} />
        <h2 className="landing__picks-title">Popular areas</h2>
        <QuickPicks onPick={onSearch} />
        <ul className="landing__points">
          <li>
            <strong>Modelled risk around each hotel</strong>
            Shown as a number from 0 to 1 and compared with the rest of London.
          </li>
          <li>
            <strong>Recorded crime and current events</strong>
            Police-recorded crime for the month shown and events reported now, within 300 m.
          </li>
          <li>
            <strong>The walk from the station</strong>
            Shortest route and a lower-risk route, with time and distance for both.
          </li>
        </ul>
        <p className="muted">No prices or availability are shown. Book on the hotel’s own website.</p>
      </div>
    </main>
  )
}
