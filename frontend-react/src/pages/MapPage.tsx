import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { MapContainer, TileLayer, useMap, useMapEvents } from 'react-leaflet'
import { divIcon, point } from 'leaflet'

import { BottomSheet } from '../components/BottomSheet'
import { CourtMarkerLayer } from '../components/CourtMarkerLayer'
import { ScheduleBannerView } from '../components/ScheduleBanner'
import { api } from '../lib/api'
import { getCurrentPosition, openExternalUrl } from '../lib/native'
import type { CourtSummary, PresenceStatus, ScheduleBannerData, UserSummary } from '../types'

interface MapPageProps {
  selectedState: string
  selectedCounty: string
  currentPresence: PresenceStatus | null
  currentUser: UserSummary | null
  onStateChange: (value: string) => void
  onCountyChange: (value: string) => void
  states: Array<{ abbr: string; name: string; court_count: number }>
  counties: Array<{ slug: string; name: string; court_count: number }>
  scheduleBanner: ScheduleBannerData | null
  mineOnly: boolean
  selectedDayKey: string | null
  onToggleMineOnly?: () => void
  onOpenScheduleDay: (dayKey: string | null) => void
  onOpenScheduleComposer: () => void
}

interface CourtsResponse {
  courts: CourtSummary[]
}

type SortMode = 'best' | 'distance' | 'activity'
type MapCourtSummary = CourtSummary & {
  scheduled_count: number
  activity_score: number
}

function buildCourtMarkerIcon({
  courtId,
  activePlayers,
  scheduledCount,
}: {
  courtId: number
  activePlayers: number
  scheduledCount: number
}) {
  const playersLabel = activePlayers > 9 ? '9+' : String(activePlayers)
  const scheduleLabel = scheduledCount > 9 ? '9+' : String(scheduledCount)
  const classes = ['map-court-marker']
  if (activePlayers > 0) classes.push('has-players')
  if (scheduledCount > 0) classes.push('has-schedule')

  return divIcon({
    className: 'map-court-marker-icon',
    html: `
      <div class="${classes.join(' ')}" data-court-id="${courtId}">
        <span class="map-court-marker-core"></span>
        ${activePlayers > 0 ? `<span class="map-court-marker-badge players">${playersLabel}</span>` : ''}
        ${scheduledCount > 0 ? `<span class="map-court-marker-badge schedule">${scheduleLabel}</span>` : ''}
      </div>
    `,
    iconSize: point(42, 54),
    iconAnchor: point(21, 42),
    popupAnchor: point(0, -34),
  })
}

function formatDistanceMiles(distance: number | null | undefined) {
  if (typeof distance !== 'number' || Number.isNaN(distance)) return null
  if (distance < 1) return `${distance.toFixed(1)} mi`
  return `${distance.toFixed(1)} mi`
}

function haversineMiles(lat1: number, lon1: number, lat2: number, lon2: number) {
  const earthRadiusMiles = 3959
  const toRadians = (value: number) => (value * Math.PI) / 180
  const dLat = toRadians(lat2 - lat1)
  const dLon = toRadians(lon2 - lon1)
  const a = (
    Math.sin(dLat / 2) ** 2
    + Math.cos(toRadians(lat1)) * Math.cos(toRadians(lat2)) * Math.sin(dLon / 2) ** 2
  )
  return earthRadiusMiles * 2 * Math.asin(Math.sqrt(a))
}

function buildSearchScore(court: CourtSummary, query: string) {
  if (!query) return 0
  const lowered = query.toLowerCase()
  const name = String(court.name || '').toLowerCase()
  const city = String(court.city || '').toLowerCase()
  const address = String(court.address || '').toLowerCase()

  if (name === lowered) return 0
  if (name.startsWith(lowered)) return 1
  if (name.includes(lowered)) return 2
  if (city.startsWith(lowered)) return 3
  if (city.includes(lowered)) return 4
  if (address.includes(lowered)) return 5
  return 6
}

function compareCourts(
  left: MapCourtSummary,
  right: MapCourtSummary,
  query: string,
  sortMode: SortMode,
) {
  const searchDelta = buildSearchScore(left, query) - buildSearchScore(right, query)
  if (query && searchDelta !== 0) return searchDelta

  if (sortMode === 'distance') {
    const leftDistance = typeof left.distance === 'number' ? left.distance : Number.POSITIVE_INFINITY
    const rightDistance = typeof right.distance === 'number' ? right.distance : Number.POSITIVE_INFINITY
    if (leftDistance !== rightDistance) return leftDistance - rightDistance
  }

  if (sortMode === 'activity') {
    if (left.activity_score !== right.activity_score) return right.activity_score - left.activity_score
    if (left.scheduled_count !== right.scheduled_count) return right.scheduled_count - left.scheduled_count
  } else {
    if (Number(left.active_players || 0) !== Number(right.active_players || 0)) {
      return Number(right.active_players || 0) - Number(left.active_players || 0)
    }
    if (left.scheduled_count !== right.scheduled_count) return right.scheduled_count - left.scheduled_count
    if (typeof left.distance === 'number' && typeof right.distance === 'number' && left.distance !== right.distance) {
      return left.distance - right.distance
    }
  }

  if (Number(left.num_courts || 0) !== Number(right.num_courts || 0)) {
    return Number(right.num_courts || 0) - Number(left.num_courts || 0)
  }

  return String(left.name || '').localeCompare(String(right.name || ''))
}

function MapViewportSync({
  center,
  animate,
  verticalOffset = 0,
}: {
  center: [number, number]
  animate: boolean
  verticalOffset?: number
}) {
  const map = useMap()
  const lastCenterKeyRef = useRef('')
  const [lat, lng] = center

  useEffect(() => {
    const nextKey = `${lat}:${lng}`
    if (lastCenterKeyRef.current === nextKey) return
    lastCenterKeyRef.current = nextKey

    const currentCenter = typeof map.getCenter === 'function' ? map.getCenter() : { lat, lng }
    if (Math.abs(currentCenter.lat - lat) < 0.0001 && Math.abs(currentCenter.lng - lng) < 0.0001) {
      if (!verticalOffset) {
        return
      }
    }

    const currentZoom = typeof map.getZoom === 'function' ? map.getZoom() : 11
    const targetZoom = animate ? Math.max(currentZoom, 13) : currentZoom
    const nextCenter = (
      verticalOffset
      && typeof map.project === 'function'
      && typeof map.unproject === 'function'
    )
      ? (() => {
          const projected = map.project([lat, lng], targetZoom)
          const adjusted = projected.subtract(point(0, verticalOffset))
          const target = map.unproject(adjusted, targetZoom)
          return [target.lat, target.lng] as [number, number]
        })()
      : [lat, lng] as [number, number]

    if (targetZoom !== currentZoom && typeof map.setView === 'function') {
      map.setView(nextCenter, targetZoom, { animate })
      return
    }

    if (animate && typeof map.panTo === 'function') {
      map.panTo(nextCenter, { animate: true, duration: 0.28 })
      return
    }

    if (typeof map.setView === 'function') {
      map.setView(nextCenter, currentZoom, { animate: false })
    }
  }, [animate, lat, lng, map, verticalOffset])

  return null
}

function MapTapClear({ onClear }: { onClear: () => void }) {
  useMapEvents({
    click(event) {
      const target = event.originalEvent.target
      if (
        target instanceof HTMLElement
        && target.closest('.leaflet-interactive, .leaflet-control, .leaflet-popup, .leaflet-marker-icon')
      ) {
        return
      }
      onClear()
    },
  })

  return null
}

export function MapPage({
  selectedState,
  selectedCounty,
  currentPresence,
  currentUser,
  onStateChange,
  onCountyChange,
  states,
  counties,
  scheduleBanner,
  mineOnly,
  selectedDayKey,
  onToggleMineOnly,
  onOpenScheduleDay,
  onOpenScheduleComposer,
}: MapPageProps) {
  const navigate = useNavigate()
  const lastMarkerSelectionAt = useRef(0)
  const [areaExpanded, setAreaExpanded] = useState(false)
  const [search, setSearch] = useState('')
  const [liveNowOnly, setLiveNowOnly] = useState(false)
  const [nearMeOnly, setNearMeOnly] = useState(false)
  const [sortMode, setSortMode] = useState<SortMode>('best')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [courtListOpen, setCourtListOpen] = useState(false)
  const [selectedCourt, setSelectedCourt] = useState<CourtSummary | null>(null)
  const [userLocation, setUserLocation] = useState<{ lat: number; lng: number } | null>(null)
  const [filterState, setFilterState] = useState({
    indoor: false,
    lighted: false,
    dedicated: false,
    free: false,
  })
  const deferredSearch = useDeferredValue(search)

  useEffect(() => {
    setSelectedCourt(null)
  }, [selectedCounty, selectedState])

  useEffect(() => {
    if ((!nearMeOnly && sortMode !== 'distance') || userLocation) return
    void getCurrentPosition()
      .then((position) => {
        setUserLocation({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
        })
      })
      .catch((error) => {
        if (nearMeOnly) {
          setNearMeOnly(false)
        }
        if (sortMode === 'distance') {
          setSortMode('best')
        }
        window.alert(error instanceof Error ? error.message : 'Unable to get your location.')
      })
  }, [nearMeOnly, sortMode, userLocation])

  const courtsQuery = useQuery({
    queryKey: ['county-courts', selectedState, selectedCounty],
    queryFn: () =>
      api.get<CourtsResponse>(
        `/api/courts?state=${encodeURIComponent(selectedState)}&county_slug=${encodeURIComponent(selectedCounty)}`,
      ),
    enabled: Boolean(selectedState && selectedCounty),
  })

  const courts = courtsQuery.data?.courts || []
  const normalizedSearch = deferredSearch.trim().toLowerCase()
  const scheduleCountByCourt = useMemo(() => {
    const counts = new Map<number, number>()
    for (const item of scheduleBanner?.items || []) {
      if (!item.court_id) continue
      counts.set(item.court_id, (counts.get(item.court_id) || 0) + 1)
    }
    return counts
  }, [scheduleBanner])
  const preparedCourts = useMemo<MapCourtSummary[]>(
    () =>
      courts.map((court) => {
        const distance = userLocation
          ? Number(haversineMiles(userLocation.lat, userLocation.lng, court.latitude, court.longitude).toFixed(1))
          : court.distance
        const scheduledCount = Math.max(
          Number(court.open_sessions || 0),
          Number(scheduleCountByCourt.get(court.id) || 0),
        )
        return {
          ...court,
          distance,
          scheduled_count: scheduledCount,
          activity_score: Number(court.active_players || 0) * 100 + scheduledCount * 10 + Number(court.num_courts || 0),
        }
      }),
    [courts, scheduleCountByCourt, userLocation],
  )
  const visibleCourts = useMemo(
    () =>
      [...preparedCourts]
        .filter((court) => {
          const searchMatch = !normalizedSearch
            || `${court.name} ${court.city || ''} ${court.address || ''}`.toLowerCase().includes(normalizedSearch)
          if (!searchMatch) return false
          if (liveNowOnly && !court.active_players) return false
          if (filterState.indoor && !court.indoor) return false
          if (filterState.lighted && !court.lighted) return false
          if (filterState.dedicated && court.court_type !== 'dedicated') return false
          if (filterState.free && String(court.fees || '').toLowerCase() !== 'free') return false
          if (nearMeOnly && typeof court.distance === 'number') {
            return court.distance <= 25
          }
          return true
        })
        .sort((left, right) => compareCourts(left, right, normalizedSearch, sortMode)),
    [
      filterState.dedicated,
      filterState.free,
      filterState.indoor,
      filterState.lighted,
      liveNowOnly,
      nearMeOnly,
      normalizedSearch,
      preparedCourts,
      sortMode,
    ],
  )

  useEffect(() => {
    if (!visibleCourts.length) {
      setSelectedCourt(null)
      return
    }
    if (!selectedCourt) return
    const matchingCourt = visibleCourts.find((court) => court.id === selectedCourt.id) || null
    if (!matchingCourt) {
      setSelectedCourt(null)
      return
    }
    if (matchingCourt !== selectedCourt) {
      setSelectedCourt(matchingCourt)
    }
  }, [selectedCourt, visibleCourts])

  const mapCenter = useMemo<[number, number]>(() => {
    if (userLocation) {
      return [userLocation.lat, userLocation.lng]
    }
    if (visibleCourts[0]) {
      return [visibleCourts[0].latitude, visibleCourts[0].longitude]
    }
    return [40.83, -124.08]
  }, [userLocation, visibleCourts])

  const liveCourtCount = visibleCourts.filter((court) => Number(court.active_players || 0) > 0).length
  const selectedStateName = states.find((state) => state.abbr === selectedState)?.name || selectedState
  const selectedCountyName = counties.find((county) => county.slug === selectedCounty)?.name || selectedCounty
  const nextScheduleByCourt = useMemo(() => {
    const nextItems = new Map<number, NonNullable<ScheduleBannerData['items']>[number]>()
    for (const item of scheduleBanner?.items || []) {
      if (!item.court_id || !item.start_time) continue
      const current = nextItems.get(item.court_id)
      if (!current || (item.start_time || '').localeCompare(current.start_time || '') < 0) {
        nextItems.set(item.court_id, item)
      }
    }
    return nextItems
  }, [scheduleBanner])
  const markerIcons = useMemo(() => {
    const icons = new Map<number, ReturnType<typeof buildCourtMarkerIcon>>()
    for (const court of visibleCourts) {
        icons.set(
          court.id,
          buildCourtMarkerIcon({
            courtId: court.id,
            activePlayers: Number(court.active_players || 0),
            scheduledCount: Number(court.scheduled_count || 0),
          }),
        )
      }
      return icons
  }, [visibleCourts])
  const showMapChrome = !selectedCourt
  const areaSummaryBits = [
    `${visibleCourts.length} ${visibleCourts.length === 1 ? 'court' : 'courts'} on the map`,
    liveCourtCount ? `${liveCourtCount} live now` : null,
    nearMeOnly ? 'near me' : null,
    liveNowOnly ? 'live only' : null,
    sortMode === 'distance' ? 'nearest first' : null,
    sortMode === 'activity' ? 'most active first' : null,
  ].filter(Boolean)
  const areaSummary = search.trim()
    ? `${visibleCourts.length} matches for "${search.trim()}"`
    : areaSummaryBits.join(' · ')
  const sortLabel = sortMode === 'distance' ? 'Nearest first' : sortMode === 'activity' ? 'Most active first' : 'Best fit'

  const handleDirections = useCallback((court: CourtSummary) => {
    void openExternalUrl(
      `https://www.google.com/maps/search/?api=1&query=${court.latitude},${court.longitude}`,
    )
  }, [])

  const handleSelectCourt = useCallback((court: CourtSummary) => {
    lastMarkerSelectionAt.current = Date.now()
    setAreaExpanded(false)
    setSelectedCourt(court)
  }, [])

  const handleCloseCourt = useCallback((courtId: number) => {
    setSelectedCourt((current) => (current?.id === courtId ? null : current))
  }, [])

  const handleOpenCourtDetails = useCallback((courtId: number) => {
    navigate(`/courts/${courtId}`)
  }, [navigate])

  return (
    <div className="page map-page">
      <div className={`map-stage map-stage-primary ${selectedCourt ? 'selection-active' : ''}`}>
        <MapContainer
          center={mapCenter}
          zoom={11}
          scrollWheelZoom
          className="leaflet-map map-primary"
        >
          <MapViewportSync
            center={mapCenter}
            animate={false}
            verticalOffset={showMapChrome ? 112 : 0}
          />
          <MapTapClear
            onClear={() => {
              if (Date.now() - lastMarkerSelectionAt.current < 250) {
                return
              }
              setSelectedCourt(null)
            }}
          />
          <TileLayer
            attribution="&copy; OpenStreetMap &copy; CARTO"
            url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
          />
          <CourtMarkerLayer
            courts={visibleCourts}
            markerIcons={markerIcons}
            selectedCourtId={selectedCourt?.id || null}
            nextScheduleByCourt={nextScheduleByCourt}
            currentPresence={currentPresence}
            focusPaddingTop={showMapChrome ? 220 : 24}
            onSelectCourt={handleSelectCourt}
            onCloseCourt={handleCloseCourt}
            onOpenCourtDetails={handleOpenCourtDetails}
            onDirections={handleDirections}
          />
        </MapContainer>

        {showMapChrome ? (
          <div className="map-overlay-stack">
            <ScheduleBannerView
              data={scheduleBanner}
              compact
              className="map-schedule-banner"
              expanded
              mineOnly={mineOnly}
              selectedDayKey={selectedDayKey}
              onToggle={() => undefined}
              onToggleMineOnly={currentUser ? onToggleMineOnly : undefined}
              onSelectDay={onOpenScheduleDay}
              onCreate={onOpenScheduleComposer}
            />

            <section className={`map-area-shell map-area-shell-inline ${areaExpanded ? 'expanded' : ''}`}>
              <div className="map-area-summary">
                <button
                  type="button"
                  className="map-area-toggle"
                  onClick={() => setAreaExpanded((value) => !value)}
                >
                  <div className="section-kicker">Area</div>
                  <div className="map-area-mainline">
                    <div className="map-area-heading">
                      <strong>{selectedCountyName}</strong>
                      <div className="map-area-meta">
                        <em>{selectedStateName}</em>
                        <span className="map-area-count">{visibleCourts.length} courts</span>
                      </div>
                    </div>
                    <span className="map-area-caret" aria-hidden="true">⌄</span>
                  </div>
                  {areaExpanded ? <span>{areaSummary}</span> : null}
                </button>

                <div className="map-area-actions">
                  <button type="button" className="chip" onClick={() => setCourtListOpen(true)}>
                    Courts
                  </button>
                </div>
              </div>

              {areaExpanded ? (
                <div className="map-area-body">
                  <div className="picker-row">
                    <select value={selectedState} onChange={(event) => onStateChange(event.target.value)}>
                      {states.map((state) => (
                        <option key={state.abbr} value={state.abbr}>
                          {state.name}
                        </option>
                      ))}
                    </select>
                    <select value={selectedCounty} onChange={(event) => onCountyChange(event.target.value)}>
                      {counties.map((county) => (
                        <option key={county.slug} value={county.slug}>
                          {county.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="chip-row compact">
                    <button
                      type="button"
                      className={nearMeOnly ? 'chip active' : 'chip'}
                      onClick={() => setNearMeOnly((value) => !value)}
                    >
                      Near Me
                    </button>
                    <button
                      type="button"
                      className={liveNowOnly ? 'chip active' : 'chip'}
                      onClick={() => setLiveNowOnly((value) => !value)}
                    >
                      Live Now
                    </button>
                    <button
                      type="button"
                      className={sortMode === 'distance' ? 'chip active' : 'chip'}
                      onClick={() => setSortMode((current) => (current === 'distance' ? 'best' : 'distance'))}
                    >
                      Distance
                    </button>
                    <button
                      type="button"
                      className={sortMode === 'activity' ? 'chip active' : 'chip'}
                      onClick={() => setSortMode((current) => (current === 'activity' ? 'best' : 'activity'))}
                    >
                      Activity
                    </button>
                    <button type="button" className="chip" onClick={() => setFiltersOpen(true)}>
                      Filters
                    </button>
                  </div>
                </div>
              ) : null}
            </section>
          </div>
        ) : null}

      </div>

      {!visibleCourts.length && !courtsQuery.isFetching ? (
        <div className="empty-card">No courts match these filters yet.</div>
      ) : null}

      <BottomSheet open={filtersOpen} title="More Filters" onClose={() => setFiltersOpen(false)}>
        <div className="sheet-grid">
          {[
            ['indoor', 'Indoor only'],
            ['lighted', 'Lighted'],
            ['dedicated', 'Dedicated courts'],
            ['free', 'Free play'],
          ].map(([key, label]) => (
            <label key={key} className="toggle-row">
              <span>{label}</span>
              <input
                type="checkbox"
                checked={filterState[key as keyof typeof filterState]}
                onChange={(event) =>
                  setFilterState((current) => ({
                    ...current,
                    [key]: event.target.checked,
                  }))
                }
              />
            </label>
          ))}
        </div>
      </BottomSheet>

      <BottomSheet
        open={courtListOpen}
        title="Find a court"
        subtitle={`${visibleCourts.length} showing · ${sortLabel}`}
        onClose={() => setCourtListOpen(false)}
        variant="action"
      >
        <div className="sheet-grid map-sheet-grid">
          <div className="map-list-toolbar">
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search court, city, or address"
              aria-label="Search courts"
            />

            <div className="segmented-control map-sort-control" role="group" aria-label="Sort courts">
              <button
                type="button"
                className={sortMode === 'best' ? 'active' : ''}
                onClick={() => setSortMode('best')}
              >
                Best
              </button>
              <button
                type="button"
                className={sortMode === 'distance' ? 'active' : ''}
                onClick={() => setSortMode('distance')}
              >
                Distance
              </button>
              <button
                type="button"
                className={sortMode === 'activity' ? 'active' : ''}
                onClick={() => setSortMode('activity')}
              >
                Activity
              </button>
            </div>

            <div className="chip-row compact map-list-chips">
              <button
                type="button"
                className={nearMeOnly ? 'chip active' : 'chip'}
                onClick={() => setNearMeOnly((value) => !value)}
              >
                Near Me
              </button>
              <button
                type="button"
                className={liveNowOnly ? 'chip active' : 'chip'}
                onClick={() => setLiveNowOnly((value) => !value)}
              >
                Live
              </button>
              <button type="button" className="chip" onClick={() => setFiltersOpen(true)}>
                Filters
              </button>
            </div>
          </div>

          {!visibleCourts.length ? (
            <div className="empty-card map-list-empty">No courts match that search right now.</div>
          ) : null}

          {visibleCourts.map((court) => (
            <button
              key={court.id}
              type="button"
              className={`rail-card map-court-list-card ${selectedCourt?.id === court.id ? 'selected' : ''}`}
              onClick={() => {
                setSelectedCourt(court)
                setAreaExpanded(false)
                setCourtListOpen(false)
              }}
            >
              <div className="map-court-list-card-top">
                <strong>{court.name}</strong>
                {formatDistanceMiles(court.distance) ? <span className="map-court-distance">{formatDistanceMiles(court.distance)}</span> : null}
              </div>
              <span>{court.city}, {court.state}{court.address ? ` · ${court.address}` : ''}</span>
              <div className="rail-card-meta">
                <span>{court.active_players || 0} live</span>
                {court.scheduled_count ? <span>{court.scheduled_count} scheduled</span> : null}
                <span>{court.num_courts || 0} courts</span>
                <span>{court.indoor ? 'Indoor' : 'Outdoor'}</span>
              </div>
            </button>
          ))}
        </div>
      </BottomSheet>
    </div>
  )
}
