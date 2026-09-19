import { formatCoord } from '../format'
import type { LngLat } from '../types'

export interface CoordinateFields {
  lng: string
  lat: string
}

interface Props {
  hover: LngLat | null
  fields: CoordinateFields
  onChange: (fields: CoordinateFields) => void
  onGo: (pos: LngLat) => void
}

// Shows the position under the cursor. Clicking the map copies that position
// into the fields, where it can be edited and used to move the map.
export function CoordinatePanel({ hover, fields, onChange, onGo }: Props) {
  const parsed = { lng: Number(fields.lng), lat: Number(fields.lat) }
  const valid =
    fields.lng !== '' && fields.lat !== '' && Math.abs(parsed.lng) <= 180 && Math.abs(parsed.lat) <= 90

  return (
    <section className="panel coords">
      <h2>Position</h2>
      <div className="hover">
        <span>Cursor</span>
        <code>{hover ? `${formatCoord(hover.lng)}, ${formatCoord(hover.lat)}` : '—'}</code>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (valid) onGo(parsed)
        }}
      >
        <label>
          Longitude
          <input
            type="number" step="0.0005" min={-180} max={180} value={fields.lng}
            onChange={(e) => onChange({ ...fields, lng: e.target.value })}
          />
        </label>
        <label>
          Latitude
          <input
            type="number" step="0.0005" min={-90} max={90} value={fields.lat}
            onChange={(e) => onChange({ ...fields, lat: e.target.value })}
          />
        </label>
        <button type="submit" disabled={!valid}>Go</button>
      </form>
      <p className="meta">Hover to read a position, click the map to copy it into the fields.</p>
    </section>
  )
}
