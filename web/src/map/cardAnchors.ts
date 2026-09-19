import type { AssistantLayer } from '../assistant'
import { bboxOf, closestOnLine, distanceM } from '../geo'

export type CardAnchor = 'top' | 'bottom' | 'left' | 'right'

// An event closer to the line than this counts as on it: its card side alternates
const ON_LINE_M = 10

/**
 * Side of its event on which each compact card opens, chosen so the cards leave the
 * route or the area centre visible. Route: a card opens on the side of the line its
 * event lies on, above or below for a route that runs mostly east-west, left or
 * right otherwise. Area: above for an event north of the centre, below otherwise.
 * The camera fit of an assistant layer sets the bearing to north, which makes
 * north the top of the screen. Computed for every event of the layer, so closing a
 * card does not move the others. The value is the MapLibre popup anchor, the
 * side of the popup that touches the position: 'bottom' opens the card above it.
 */
export function cardAnchors({ route, area, events }: Pick<AssistantLayer, 'route' | 'area' | 'events'>): Map<string, CardAnchor> {
  const anchors = new Map<string, CardAnchor>()
  const line = route?.safe.geometry.coordinates
  if (line) {
    const bbox = bboxOf(line)!
    const eastWest = distanceM([bbox[0], bbox[1]], [bbox[2], bbox[1]]) >= distanceM([bbox[0], bbox[1]], [bbox[0], bbox[3]])
    let alternate = false
    for (const e of events) {
      const p: [number, number] = [e.properties.lng, e.properties.lat]
      const { point, distance_m } = closestOnLine(p, line)
      let positive = eastWest ? p[1] > point[1] : p[0] > point[0]
      if (distance_m < ON_LINE_M) {
        positive = alternate
        alternate = !alternate
      }
      anchors.set(e.properties.id, eastWest ? (positive ? 'bottom' : 'top') : positive ? 'left' : 'right')
    }
  } else if (area) {
    for (const e of events) anchors.set(e.properties.id, e.properties.lat >= area.center[1] ? 'bottom' : 'top')
  }
  return anchors
}
