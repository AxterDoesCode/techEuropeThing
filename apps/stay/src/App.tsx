import { lazy, Suspense, useCallback, useMemo, useState } from 'react'
import { fetchHotels, type Hotel, type HotelsResponse } from './api'
import { AboutData } from './components/AboutData'
import { CompareTray, MAX_PINNED } from './components/CompareTray'
import { DetailPanel } from './components/DetailPanel'
import { HotelList } from './components/HotelList'
import { Landing } from './components/Landing'
import { QuickPicks } from './components/QuickPicks'
import { ResultsToolbar } from './components/ResultsToolbar'
import { SearchBar } from './components/SearchBar'
import { ErrorNote, Loading } from './components/StatusBlocks'
import { subtypeOf, websiteUrl } from './hotel'
import { useHotelArea, useStationWalk } from './hooks/useHotelData'
import { useMediaQuery } from './hooks/useMediaQuery'
import { useRequest } from './hooks/useRequest'
import { useUrlState } from './hooks/useUrlState'
import { formatDistance } from './risk'
import type { SearchPlace } from './urlState'

// maplibre-gl is about 1 MB; it is loaded only when results are shown.
const MapView = lazy(() => import('./components/MapView').then((m) => ({ default: m.MapView })))

const DRAWER_WIDTH = 420

function countSentence(data: HotelsResponse, shown: number, place: SearchPlace): string {
  const where = `within ${formatDistance(data.radius_m)} of ${place.label}`
  if (data.total === 0) return `No places to stay ${where}`
  const returned = data.hotels.length
  const capped = data.total > returned ? ` The service returns at most ${returned} per search, chosen by the selected sort order.` : ''
  return `Showing ${shown} of ${data.total} places to stay ${where}.${capped}`
}

export default function App() {
  const [url, setUrl] = useUrlState()
  const { place, radius, sort, types, websiteOnly, hotelId } = url
  const isMobile = useMediaQuery('(max-width: 860px)')
  const [mobileView, setMobileView] = useState<'list' | 'map'>('list')
  const [sheetHidden, setSheetHidden] = useState(false)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [pinned, setPinned] = useState<Hotel[]>([])
  const [retry, setRetry] = useState({ hotels: 0, area: 0, route: 0 })

  // One request per search, ordered by modelled risk. "Closest" is sorted in the browser.
  // The service returns at most 60 hotels; only when the search has more than that does
  // "Closest" need its own request, because the 60 closest differ from the 60 with the lowest risk.
  const searchKey = place ? `${place.lat},${place.lng},${radius}` : null
  const byRisk = useRequest(
    searchKey ? `hotels:${searchKey},safety` : null,
    (signal) => fetchHotels({ lat: place!.lat, lng: place!.lng, radius_m: radius, sort: 'safety' }, signal),
    retry.hotels,
  )
  const truncated = byRisk.status === 'success' && byRisk.data.total > byRisk.data.hotels.length
  const byDistance = useRequest(
    searchKey && sort === 'distance' && truncated ? `hotels:${searchKey},distance` : null,
    (signal) => fetchHotels({ lat: place!.lat, lng: place!.lng, radius_m: radius, sort: 'distance' }, signal),
    retry.hotels,
  )
  const hotelsState = sort === 'distance' && truncated ? byDistance : byRisk
  const allHotels = useMemo(() => {
    if (hotelsState.status !== 'success') return null
    const hotels = [...hotelsState.data.hotels]
    if (sort === 'distance') hotels.sort((a, b) => a.distance_m - b.distance_m)
    else hotels.sort((a, b) => (a.risk?.mean_score ?? Infinity) - (b.risk?.mean_score ?? Infinity))
    return hotels
  }, [hotelsState, sort])

  const visible = useMemo(
    () =>
      (allHotels ?? []).filter(
        (h) => (types.length === 0 || types.includes(subtypeOf(h))) && (!websiteOnly || websiteUrl(h) !== null),
      ),
    [allHotels, types, websiteOnly],
  )

  const selected = useMemo(
    () => (hotelId ? (allHotels?.find((h) => h.id === hotelId) ?? pinned.find((h) => h.id === hotelId) ?? null) : null),
    [hotelId, allHotels, pinned],
  )
  const area = useHotelArea(selected, retry.area)
  const route = useStationWalk(selected, retry.route)

  const search = useCallback(
    (next: SearchPlace) => {
      setUrl({ place: next, hotelId: null }, true)
      setMobileView('list')
    },
    [setUrl],
  )
  const select = useCallback(
    (id: string) => {
      setUrl({ hotelId: id })
      setSheetHidden(false)
    },
    [setUrl],
  )
  const closeDetail = useCallback(() => setUrl({ hotelId: null }), [setUrl])
  const togglePin = useCallback((hotel: Hotel) => {
    setPinned((current) => {
      if (current.some((h) => h.id === hotel.id)) return current.filter((h) => h.id !== hotel.id)
      return current.length >= MAX_PINNED ? current : [...current, hotel]
    })
  }, [])

  if (!place) {
    return (
      <div className="page">
        <Header />
        <Landing radius={radius} onSearch={search} onRadiusChange={(r) => setUrl({ radius: r })} />
        <Footer />
      </div>
    )
  }

  const pinnedIds = pinned.map((h) => h.id)
  const pinDisabled = pinned.length >= MAX_PINNED
  const showDetail = selected !== null && !(isMobile && sheetHidden)
  const showList = !isMobile || mobileView === 'list'
  const showMap = !isMobile || mobileView === 'map'

  return (
    <div className="page page--results">
      <Header>
        <SearchBar compact place={place} radius={radius} onSearch={search} onRadiusChange={(r) => setUrl({ radius: r, hotelId: null }, true)} />
      </Header>
      <main className="results">
        <section className="results__list" hidden={!showList} aria-label="Search results">
          <h1 className="results__title">Places to stay near {place.label}</h1>
          <QuickPicks onPick={search} />
          <ResultsToolbar
            sort={sort}
            types={types}
            websiteOnly={websiteOnly}
            onSort={(s) => setUrl({ sort: s })}
            onTypes={(t) => setUrl({ types: t })}
            onWebsiteOnly={(w) => setUrl({ websiteOnly: w })}
          />
          {hotelsState.status === 'loading' && <Loading>Loading places to stay near {place.label}…</Loading>}
          {hotelsState.status === 'error' && (
            <ErrorNote onRetry={() => setRetry((r) => ({ ...r, hotels: r.hotels + 1 }))}>
              {hotelsState.error.status === 503
                ? 'The hotel data is not available on the service yet.'
                : `The hotel list could not be loaded. ${hotelsState.error.message}`}
            </ErrorNote>
          )}
          {hotelsState.status === 'success' && (
            <>
              <p className="results__count" role="status" data-testid="count">
                {countSentence(hotelsState.data, visible.length, place)}
              </p>
              {hotelsState.data.total > 0 && visible.length === 0 && (
                <div className="status status--info">
                  <p>No result matches the selected filters.</p>
                  <button type="button" className="button button--small" onClick={() => setUrl({ types: [], websiteOnly: false })}>
                    Clear filters
                  </button>
                </div>
              )}
              {hotelsState.data.total === 0 && (
                <p className="status status--info">Try a larger radius or another area.</p>
              )}
              <HotelList
                hotels={visible}
                placeLabel={place.label}
                selectedId={selected?.id ?? null}
                hoveredId={hoveredId}
                pinnedIds={pinnedIds}
                pinDisabled={pinDisabled}
                onSelect={select}
                onHover={setHoveredId}
                onTogglePin={togglePin}
              />
              <p className="muted results__attribution">Hotel and station data © OpenStreetMap contributors.</p>
            </>
          )}
        </section>
        <section className="results__map" hidden={!showMap} aria-label="Map">
          <Suspense fallback={<Loading>Loading the map…</Loading>}>
          <MapView
            place={place}
            radius={radius}
            hotels={visible}
            hoveredId={hoveredId}
            selected={selected}
            route={route.status === 'success' ? route.data : null}
            coveredLeft={showDetail && !isMobile ? DRAWER_WIDTH : 0}
            onHover={setHoveredId}
            onSelect={select}
          />
          </Suspense>
          {isMobile && selected && sheetHidden && (
            <button type="button" className="button button--primary results__back-to-detail" onClick={() => setSheetHidden(false)}>
              Back to {selected.name} details
            </button>
          )}
        </section>
        {showDetail && selected && (
          <DetailPanel
            hotel={selected}
            area={area}
            route={route}
            pinned={pinnedIds.includes(selected.id)}
            pinDisabled={pinDisabled}
            onTogglePin={togglePin}
            onRetryArea={() => setRetry((r) => ({ ...r, area: r.area + 1 }))}
            onRetryRoute={() => setRetry((r) => ({ ...r, route: r.route + 1 }))}
            onClose={closeDetail}
            onShowMap={
              isMobile
                ? () => {
                    setSheetHidden(true)
                    setMobileView('map')
                  }
                : undefined
            }
          />
        )}
      </main>
      <CompareTray pinned={pinned} onRemove={togglePin} onSelect={(h) => select(h.id)} onClear={() => setPinned([])} />
      {isMobile && (
        <div className="view-toggle" role="group" aria-label="Results view">
          <button type="button" aria-pressed={mobileView === 'list'} onClick={() => setMobileView('list')}>
            List
          </button>
          <button type="button" aria-pressed={mobileView === 'map'} onClick={() => setMobileView('map')}>
            Map
          </button>
        </div>
      )}
    </div>
  )
}

function Header({ children }: { children?: React.ReactNode }) {
  return (
    <header className="header">
      <a className="brand" href={window.location.pathname} aria-label="Stay London, start page">
        <span className="brand__mark" aria-hidden="true" />
        Stay London
      </a>
      <div className="header__search">{children}</div>
      <AboutData />
    </header>
  )
}

function Footer() {
  return (
    <footer className="footer">
      Hotels, stations and map © OpenStreetMap contributors. Risk values are modelled, not probabilities.
    </footer>
  )
}
