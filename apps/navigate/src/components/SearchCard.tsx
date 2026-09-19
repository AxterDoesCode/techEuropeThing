import type { Place } from '../geocode'
import type { EndpointKey } from '../types'
import type { Endpoint } from '../urlState'
import { PlaceInput } from './PlaceInput'

interface Props {
  origin: Endpoint | null
  destination: Endpoint | null
  picking: EndpointKey | null
  locating: boolean
  locationError: string | null
  onFocus: (endpoint: EndpointKey) => void
  onSelect: (endpoint: EndpointKey, place: Place) => void
  onClear: (endpoint: EndpointKey) => void
  onSwap: () => void
  onLocate: () => void
}

export function SearchCard(props: Props) {
  const { origin, destination, picking, locating, locationError } = props
  return (
    <section className="search" aria-label="Trip">
      <div className="search-fields">
        <PlaceInput
          label="From"
          placeholder="Starting point"
          value={origin?.name ?? ''}
          active={picking === 'origin'}
          onFocus={() => props.onFocus('origin')}
          onSelect={(place) => props.onSelect('origin', place)}
          onClear={() => props.onClear('origin')}
        />
        <PlaceInput
          label="To"
          placeholder="Destination"
          value={destination?.name ?? ''}
          active={picking === 'destination'}
          onFocus={() => props.onFocus('destination')}
          onSelect={(place) => props.onSelect('destination', place)}
          onClear={() => props.onClear('destination')}
        />
      </div>
      <button
        type="button"
        className="icon-button swap"
        aria-label="Swap From and To"
        title="Swap From and To"
        disabled={!origin && !destination}
        onClick={props.onSwap}
      >
        ⇅
      </button>
      <div className="search-actions">
        <button type="button" className="link-button" onClick={props.onLocate} disabled={locating}>
          {locating ? 'Locating…' : 'Use my location'}
        </button>
        {picking && (
          <span className="hint" aria-live="polite">
            Click the map to set {picking === 'origin' ? 'From' : 'To'}
          </span>
        )}
      </div>
      {locationError && <p className="notice error" role="alert">{locationError}</p>}
    </section>
  )
}
