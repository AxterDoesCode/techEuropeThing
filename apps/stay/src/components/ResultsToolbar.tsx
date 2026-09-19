import type { HotelSort, HotelSubtype } from '../api'
import { SUBTYPES } from '../urlState'

interface Props {
  sort: HotelSort
  types: HotelSubtype[]
  websiteOnly: boolean
  onSort: (sort: HotelSort) => void
  onTypes: (types: HotelSubtype[]) => void
  onWebsiteOnly: (value: boolean) => void
}

export function ResultsToolbar({ sort, types, websiteOnly, onSort, onTypes, onWebsiteOnly }: Props) {
  function toggleType(value: HotelSubtype) {
    onTypes(types.includes(value) ? types.filter((t) => t !== value) : [...types, value])
  }
  return (
    <div className="toolbar">
      <label className="toolbar__sort">
        <span>Sort by</span>
        <select value={sort} onChange={(e) => onSort(e.target.value === 'distance' ? 'distance' : 'safety')}>
          <option value="safety">Lowest modelled risk</option>
          <option value="distance">Closest</option>
        </select>
      </label>
      <fieldset className="toolbar__filters">
        <legend className="visually-hidden">Filter results</legend>
        {SUBTYPES.map((s) => (
          <label key={s.value} className={types.includes(s.value) ? 'chip chip--check is-on' : 'chip chip--check'}>
            <input type="checkbox" checked={types.includes(s.value)} onChange={() => toggleType(s.value)} />
            {s.label}
          </label>
        ))}
        <label className={websiteOnly ? 'chip chip--check is-on' : 'chip chip--check'}>
          <input type="checkbox" checked={websiteOnly} onChange={(e) => onWebsiteOnly(e.target.checked)} />
          Has website
        </label>
      </fieldset>
    </div>
  )
}
