import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { forwardRef, useImperativeHandle } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MapPage } from './MapPage'
import { api } from '../lib/api'
import { getCurrentPosition } from '../lib/native'
import type { CourtSummary, PresenceStatus, ScheduleBannerData } from '../types'

let mapEventHandlers: { click?: (event: { originalEvent: { target: HTMLElement } }) => void } = {}

vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => <div data-testid="map-shell">{children}</div>,
  TileLayer: () => null,
  Marker: forwardRef(
    (
      {
        eventHandlers,
      }: {
        eventHandlers?: { click?: () => void; popupopen?: () => void; popupclose?: () => void }
      },
      ref: React.ForwardedRef<{ openPopup: () => void; closePopup: () => void }>,
    ) => {
      useImperativeHandle(ref, () => ({
        openPopup: () => undefined,
        closePopup: () => undefined,
      }))

      return (
        <button type="button" data-testid="map-marker" onClick={() => eventHandlers?.click?.()}>
          marker
        </button>
      )
    },
  ),
  Popup: ({ children }: { children?: React.ReactNode }) => <div role="dialog">{children}</div>,
  useMap: () => ({
    getCenter: () => ({ lat: 40.8, lng: -124.1 }),
    getZoom: () => 11,
    panTo: vi.fn(),
    setView: vi.fn(),
  }),
  useMapEvents: (handlers: typeof mapEventHandlers) => {
    mapEventHandlers = handlers
    return null
  },
}))

vi.mock('../components/CourtMarkerLayer', () => ({
  CourtMarkerLayer: ({
    courts,
    selectedCourtId,
    onSelectCourt,
    onOpenCourtDetails,
  }: {
    courts: CourtSummary[]
    selectedCourtId: number | null
    onSelectCourt: (court: CourtSummary) => void
    onOpenCourtDetails: (courtId: number) => void
  }) => (
    <>
      {courts.map((court) => (
        <div key={court.id}>
          <button type="button" data-testid="map-marker" onClick={() => onSelectCourt(court)}>
            marker
          </button>
          {selectedCourtId === court.id ? (
            <div role="dialog">
              <div className="court-popup-card">
                <h3>{court.name}</h3>
                <button type="button" className="popup-action-btn" onClick={() => onOpenCourtDetails(court.id)}>
                  Court Details
                </button>
              </div>
            </div>
          ) : null}
        </div>
      ))}
    </>
  ),
}))

vi.mock('../lib/api', () => ({
  api: {
    get: vi.fn(),
  },
}))

vi.mock('../lib/native', () => ({
  getCurrentPosition: vi.fn(),
  openExternalUrl: vi.fn(),
}))

const mockedApiGet = vi.mocked(api.get)
const mockedGetCurrentPosition = vi.mocked(getCurrentPosition)

const courts: CourtSummary[] = [
  {
    id: 7,
    name: 'Larson Park',
    city: 'Eureka',
    state: 'CA',
    address: '1011 Waterfront Dr',
    latitude: 40.8,
    longitude: -124.1,
    num_courts: 4,
    indoor: false,
    active_players: 3,
  },
  {
    id: 8,
    name: 'Adorni Center',
    city: 'Eureka',
    state: 'CA',
    latitude: 40.79,
    longitude: -124.09,
    num_courts: 3,
    indoor: true,
    active_players: 0,
  },
]

const scheduleBanner: ScheduleBannerData = {
  items: [
    {
      id: 'session-17',
      reference_id: 17,
      item_type: 'session',
      title: 'After Work Doubles',
      subtitle: 'Larson Park',
      court_id: 7,
      court_name: 'Larson Park',
      start_time: '2026-03-21T18:00:00',
    },
  ],
  days: [],
  context: {
    county_slug: 'humboldt',
    user_only: false,
  },
}

function renderMapPage() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <MapPage
          selectedState="CA"
          selectedCounty="humboldt"
          currentPresence={null}
          currentUser={null}
          onStateChange={() => {}}
          onCountyChange={() => {}}
          states={[{ abbr: 'CA', name: 'California', court_count: 2 }]}
          counties={[{ slug: 'humboldt', name: 'Humboldt', court_count: 2 }]}
          scheduleBanner={scheduleBanner}
          mineOnly={false}
          selectedDayKey={null}
          onOpenScheduleDay={() => {}}
          onOpenScheduleComposer={() => {}}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function renderMapPageWithPresence(currentPresence: PresenceStatus | null) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <MapPage
          selectedState="CA"
          selectedCounty="humboldt"
          currentPresence={currentPresence}
          currentUser={null}
          onStateChange={() => {}}
          onCountyChange={() => {}}
          states={[{ abbr: 'CA', name: 'California', court_count: 2 }]}
          counties={[{ slug: 'humboldt', name: 'Humboldt', court_count: 2 }]}
          scheduleBanner={scheduleBanner}
          mineOnly={false}
          selectedDayKey={null}
          onOpenScheduleDay={() => {}}
          onOpenScheduleComposer={() => {}}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('MapPage', () => {
  beforeEach(() => {
    mockedApiGet.mockResolvedValue({ courts })
    mockedGetCurrentPosition.mockReset()
    mapEventHandlers = {}
  })

  afterEach(() => {
    cleanup()
    mockedApiGet.mockReset()
    mockedGetCurrentPosition.mockReset()
  })

  it('opens a compact court popup from a map pin and hides the larger map chrome while it is active', async () => {
    const user = userEvent.setup()

    renderMapPage()

    const markers = await screen.findAllByTestId('map-marker')
    expect(screen.getByText(/^Humboldt$/i)).toBeInTheDocument()
    expect(screen.getByText(/^California$/i)).toBeInTheDocument()

    await user.click(markers[0])

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Court Details/i })).toBeInTheDocument()
    })

    expect(screen.getByText(/Larson Park/i)).toBeInTheDocument()
    expect(screen.queryByText(/^Humboldt$/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Schedule$/i)).not.toBeInTheDocument()
  })

  it('opens the courts sheet and lets the user jump into the same popup flow', async () => {
    const user = userEvent.setup()

    renderMapPage()

    await user.click(screen.getByRole('button', { name: /^Courts$/i }))

    expect(screen.getByRole('dialog', { name: /Find a court/i })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Adorni Center/i }))

    await waitFor(() => {
      expect(screen.getByText(/Adorni Center/i)).toBeInTheDocument()
    })

    expect(screen.getByRole('button', { name: /Court Details/i })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: /Find a court/i })).not.toBeInTheDocument()
  })

  it('supports searching and sorting courts in the courts sheet', async () => {
    const user = userEvent.setup()

    mockedGetCurrentPosition.mockResolvedValue({
      coords: {
        latitude: 40.7902,
        longitude: -124.0901,
      },
    } as GeolocationPosition)

    renderMapPage()

    await user.click(screen.getByRole('button', { name: /^Courts$/i }))
    await user.type(screen.getByRole('searchbox', { name: /Search courts/i }), 'Adorni')

    expect(screen.getByRole('button', { name: /Adorni Center/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Larson Park/i })).not.toBeInTheDocument()

    await user.clear(screen.getByRole('searchbox', { name: /Search courts/i }))
    await user.click(screen.getByRole('button', { name: /^Distance$/i }))

    await waitFor(() => {
      expect(mockedGetCurrentPosition).toHaveBeenCalled()
    })

    const courtButtons = screen.getAllByRole('button').filter((button) => (
      /Larson Park|Adorni Center/.test(button.textContent || '')
    ))
    expect(courtButtons[0]).toHaveTextContent(/Adorni Center/i)

    await user.click(screen.getByRole('button', { name: /^Activity$/i }))

    const activitySortedButtons = screen.getAllByRole('button').filter((button) => (
      /Larson Park|Adorni Center/.test(button.textContent || '')
    ))
    expect(activitySortedButtons[0]).toHaveTextContent(/Larson Park/i)
  })

  it('does not show the old live strip on the map even when the user is checked in', async () => {
    renderMapPageWithPresence({
      checked_in: true,
      court_id: 7,
      court_name: 'Larson Park',
      looking_for_game: true,
    })

    await screen.findAllByTestId('map-marker')

    expect(screen.queryByText(/^Live$/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Open$/i })).not.toBeInTheDocument()
  })
})
