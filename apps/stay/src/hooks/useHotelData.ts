import { fetchArea, fetchRoute, type AreaResponse, type Hotel, type RouteResponse } from '../api'
import { useRequest, type RequestState } from './useRequest'

export const AREA_RADIUS_M = 300

/** Recorded crime and current events within 300 m of a hotel. */
export function useHotelArea(hotel: Hotel | null, retryToken = 0): RequestState<AreaResponse> {
  return useRequest(
    hotel ? `area:${hotel.id}` : null,
    (signal) => fetchArea({ lat: hotel!.lat, lng: hotel!.lng, radius_m: AREA_RADIUS_M }, signal),
    retryToken,
  )
}

/** Walking routes from the nearest station to the hotel. Idle when the hotel has no station. */
export function useStationWalk(hotel: Hotel | null, retryToken = 0): RequestState<RouteResponse> {
  const station = hotel?.nearest_station ?? null
  return useRequest(
    hotel && station ? `route:${hotel.id}` : null,
    (signal) => fetchRoute([station!.lng, station!.lat], [hotel!.lng, hotel!.lat], signal),
    retryToken,
  )
}
