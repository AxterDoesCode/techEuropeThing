import { useCallback, useEffect, useRef, useState } from 'react'
import type { LngLat } from './api'
import { AboutData } from './components/AboutData'
import { AlongRoute } from './components/AlongRoute'
import { MapView } from './components/MapView'
import { Preferences } from './components/Preferences'
import { RouteCards } from './components/RouteCards'
import { SearchCard } from './components/SearchCard'
import { StepList } from './components/StepList'
import { inLondon } from './geo'
import { reverseGeocode, type Place } from './geocode'
import type { EndpointKey, RouteKey } from './types'
import { coordinateLabel, readUrl, writeUrl, type Endpoint } from './urlState'
import { useAlongRoute } from './useAlongRoute'
import { useRoute } from './useRoute'

const OUTSIDE_LONDON = 'That point is outside Greater London, which this service does not cover.'

function geolocationMessage(error: GeolocationPositionError): string {
  if (error.code === error.PERMISSION_DENIED) {
    return 'Location permission was denied. Type a starting point or click the map instead.'
  }
  if (error.code === error.TIMEOUT) return 'Finding your location took too long. Retry, or type a starting point.'
  return 'Your location is not available. Type a starting point or click the map instead.'
}

export function App() {
  const [initial] = useState(() => readUrl(window.location.search))
  const [origin, setOrigin] = useState<Endpoint | null>(initial.origin)
  const [destination, setDestination] = useState<Endpoint | null>(initial.destination)
  const [alpha, setAlpha] = useState(initial.alpha)
  const [night, setNight] = useState(initial.night)
  const [selected, setSelected] = useState<RouteKey>('safe')
  const [picking, setPicking] = useState<EndpointKey | null>(null)
  const [highlight, setHighlight] = useState<LngLat | null>(null)
  const [locating, setLocating] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [routeAttempt, setRouteAttempt] = useState(0)
  const [areaAttempt, setAreaAttempt] = useState(0)
  const [fitToken, setFitToken] = useState(0)
  const [expanded, setExpanded] = useState(true)

  const routeState = useRoute(origin?.point ?? null, destination?.point ?? null, alpha, night, routeAttempt)
  const route = routeState.status === 'ready' ? routeState.data : routeState.status === 'loading' ? routeState.previous : null
  const shownRoute = origin && destination ? route : null
  const along = useAlongRoute(routeState.status === 'ready' ? routeState.data[selected] : null, areaAttempt)

  useEffect(() => writeUrl({ origin, destination, alpha, night }), [origin, destination, alpha, night])

  useEffect(() => {
    document.documentElement.dataset.theme = night ? 'night' : 'day'
  }, [night])

  // Fit the map each time a new route response arrives.
  const readyData = routeState.status === 'ready' ? routeState.data : null
  useEffect(() => {
    if (!readyData) return
    setHighlight(null)
    setFitToken((t) => t + 1)
  }, [readyData])

  // One reverse-geocoding request per endpoint at a time; a newer point aborts the older request.
  const namingRef = useRef<Record<EndpointKey, AbortController | null>>({ origin: null, destination: null })

  const setEndpoint = useCallback((key: EndpointKey, value: Endpoint | null) => {
    namingRef.current[key]?.abort()
    namingRef.current[key] = null
    ;(key === 'origin' ? setOrigin : setDestination)(value)
  }, [])

  // Sets a point that has no name yet, then replaces the coordinate label when reverse geocoding answers.
  const setPoint = useCallback(
    (key: EndpointKey, point: LngLat, fallbackName?: string) => {
      if (!inLondon(point)) {
        setNotice(OUTSIDE_LONDON)
        return false
      }
      setNotice(null)
      const label = fallbackName ?? coordinateLabel(point)
      setEndpoint(key, { point, name: label })
      const controller = new AbortController()
      namingRef.current[key] = controller
      reverseGeocode(point, controller.signal)
        .then((name) => {
          if (!name || controller.signal.aborted) return
          const named = fallbackName ? `${fallbackName} (${name})` : `Near ${name}`
          ;(key === 'origin' ? setOrigin : setDestination)((current) =>
            current && current.point === point ? { point, name: named } : current,
          )
        })
        .catch(() => undefined)
      return true
    },
    [setEndpoint],
  )

  // Endpoints restored from a URL without names get a name once.
  useEffect(() => {
    if (initial.origin && initial.origin.name === coordinateLabel(initial.origin.point)) setPoint('origin', initial.origin.point)
    if (initial.destination && initial.destination.name === coordinateLabel(initial.destination.point)) {
      setPoint('destination', initial.destination.point)
    }
    const naming = namingRef.current
    return () => {
      naming.origin?.abort()
      naming.destination?.abort()
    }
  }, [initial, setPoint])

  const onPick = (key: EndpointKey, point: LngLat) => {
    if (!setPoint(key, point)) return
    // After From is set by a click, the next click sets To when it is still empty.
    setPicking(key === 'origin' && !destination ? 'destination' : null)
  }

  const onSelectPlace = (key: EndpointKey, place: Place) => {
    setNotice(null)
    setEndpoint(key, { point: place.point, name: place.name })
    setPicking(null)
  }

  const onSwap = () => {
    const [o, d] = [origin, destination]
    setEndpoint('origin', d)
    setEndpoint('destination', o)
  }

  const onLocate = () => {
    if (!('geolocation' in navigator)) {
      setNotice('This browser does not provide a location. Type a starting point or click the map instead.')
      return
    }
    setLocating(true)
    setNotice(null)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLocating(false)
        setPoint('origin', [position.coords.longitude, position.coords.latitude], 'My location')
        setPicking(null)
      },
      (error) => {
        setLocating(false)
        setNotice(geolocationMessage(error))
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 },
    )
  }

  const onSelectRoute = (key: RouteKey) => {
    setSelected(key)
    setHighlight(null)
    setFitToken((t) => t + 1)
  }

  const hasTrip = Boolean(origin && destination)

  return (
    <div className="app">
      <MapView
        origin={origin?.point ?? null}
        destination={destination?.point ?? null}
        route={shownRoute}
        selected={selected}
        night={night}
        highlight={highlight}
        picking={picking}
        fitToken={fitToken}
        onPick={onPick}
        onMove={(key, point) => setPoint(key, point)}
      />
      <main className={expanded ? 'panel' : 'panel collapsed'}>
        <button
          type="button"
          className="sheet-toggle"
          aria-expanded={expanded}
          aria-controls="panel-details"
          onClick={() => setExpanded((e) => !e)}
        >
          <span className="sheet-grip" aria-hidden="true" />
          <span className="sr-only">{expanded ? 'Collapse details' : 'Expand details'}</span>
        </button>
        <h1 className="sr-only">Lower-risk walking routes in London</h1>
        <SearchCard
          origin={origin}
          destination={destination}
          picking={picking}
          locating={locating}
          locationError={notice}
          onFocus={setPicking}
          onSelect={onSelectPlace}
          onClear={(key) => setEndpoint(key, null)}
          onSwap={onSwap}
          onLocate={onLocate}
        />
        <div id="panel-details" className="details">
          <Preferences alpha={alpha} night={night} onAlpha={setAlpha} onNight={setNight} />
          <div className="live" aria-live="polite">
            {!hasTrip && <p className="fine empty">Enter a start and a destination, or click the map, to compare walking routes.</p>}
            {routeState.status === 'loading' && <p className="status" role="status"><span className="spinner" aria-hidden="true" />Finding routes…</p>}
            {routeState.status === 'error' && (
              <div className="notice error" role="alert">
                <p><strong>{routeState.outsideArea ? 'No route for these points' : 'Route request failed'}</strong></p>
                <p>{routeState.message}</p>
                {routeState.outsideArea ? (
                  <p>Routing currently covers inner London. Move the markers inside that area.</p>
                ) : (
                  <button type="button" className="link-button" onClick={() => setRouteAttempt((a) => a + 1)}>Retry</button>
                )}
              </div>
            )}
          </div>
          {shownRoute && origin && destination && (
            <div className={routeState.status === 'loading' ? 'results stale' : 'results'}>
              <RouteCards route={shownRoute} selected={selected} onSelect={onSelectRoute} />
              <StepList leg={shownRoute[selected]} fromName={origin.name} toName={destination.name} onHighlight={setHighlight} />
              <AlongRoute state={along} onRetry={() => setAreaAttempt((a) => a + 1)} />
            </div>
          )}
          <AboutData />
        </div>
      </main>
    </div>
  )
}
