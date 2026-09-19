import type { Category } from '../types'

type RGB = [number, number, number]

// Sequential scale for score in [0, 1]: dark blue -> yellow -> red
const STOPS: [number, RGB][] = [
  [0.0, [70, 130, 200]],
  [0.25, [90, 190, 180]],
  [0.5, [240, 200, 80]],
  [0.75, [240, 120, 50]],
  [1.0, [210, 30, 40]],
]

export function scoreColor(score: number, alpha = 200): [number, number, number, number] {
  const s = Math.min(1, Math.max(0, score))
  for (let i = 1; i < STOPS.length; i++) {
    const [t1, c1] = STOPS[i]
    const [t0, c0] = STOPS[i - 1]
    if (s <= t1) {
      const f = (s - t0) / (t1 - t0)
      return [
        c0[0] + f * (c1[0] - c0[0]),
        c0[1] + f * (c1[1] - c0[1]),
        c0[2] + f * (c1[2] - c0[2]),
        alpha,
      ]
    }
  }
  return [...STOPS[STOPS.length - 1][1], alpha]
}

export const CATEGORY_COLOR: Record<Category, RGB> = {
  violent_crime: [230, 60, 70],
  property_crime: [240, 130, 60],
  disorder: [200, 90, 200],
  fire: [255, 90, 20],
  road_closure: [250, 200, 70],
  transit_disruption: [90, 170, 250],
  flood: [60, 120, 240],
  air_quality: [150, 200, 120],
  weather: [170, 190, 210],
  other: [180, 180, 180],
}

export const CATEGORY_LABEL: Record<Category, string> = {
  violent_crime: 'Violent crime',
  property_crime: 'Property crime',
  disorder: 'Disorder',
  fire: 'Fire',
  road_closure: 'Road disruption',
  transit_disruption: 'Transit disruption',
  flood: 'Flood',
  air_quality: 'Air quality',
  weather: 'Weather',
  other: 'Other',
}
