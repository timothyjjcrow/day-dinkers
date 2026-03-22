import { Suspense, lazy, useEffect, useState, startTransition } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { io } from 'socket.io-client'

import { AuthSheet } from './components/AuthSheet'
import { NotificationsSheet } from './components/NotificationsSheet'
import { ScheduleComposerSheet } from './components/ScheduleComposerSheet'
import { ScheduleDaySheet } from './components/ScheduleDaySheet'
import { ScheduleItemSheet } from './components/ScheduleItemSheet'
import { api, clearSession, getStoredToken } from './lib/api'
import { useNativeKeyboardClass } from './lib/native'
import {
  getConfiguredApiBaseUrl,
  getPublicAppUrl,
  getSocketServerUrl,
  isNativePlatform,
  setConfiguredApiBaseUrl,
  setPublicAppUrl,
} from './lib/runtime'
import { resolveDaySelection } from './lib/schedule'
import thirdShotLogo from './assets/third-shot-logo.png'
import type {
  BannerItem,
  BootstrapData,
  CourtHubData,
  CourtSummary,
  NotificationItem,
  PlaySessionData,
  PresenceStatus,
  ScheduleBannerData,
  UserSummary,
} from './types'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 20_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

const MapPage = lazy(async () => {
  const module = await import('./pages/MapPage')
  return { default: module.MapPage }
})

const CourtPage = lazy(async () => {
  const module = await import('./pages/CourtPage')
  return { default: module.CourtPage }
})

const InboxPage = lazy(async () => {
  const module = await import('./pages/InboxPage')
  return { default: module.InboxPage }
})

const ProfilePage = lazy(async () => {
  const module = await import('./pages/ProfilePage')
  return { default: module.ProfilePage }
})

interface CourtsResponse {
  courts: CourtSummary[]
}

function formatLocalDateTime(date: Date) {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}

function formatDayTitle(dayKey: string | null) {
  if (!dayKey) return 'Scheduled games'
  return new Date(`${dayKey}T00:00:00`).toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

function BrandMark() {
  return (
    <div className="brand-lockup">
      <img className="brand-logo" src={thirdShotLogo} alt="Third Shot" />
      <p>Find courts, schedule fast, and jump into ranked play.</p>
    </div>
  )
}

function InboxIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M4 6.75A2.75 2.75 0 0 1 6.75 4h10.5A2.75 2.75 0 0 1 20 6.75v10.5A2.75 2.75 0 0 1 17.25 20H6.75A2.75 2.75 0 0 1 4 17.25V6.75Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <path
        d="m6.5 8.25 5.5 4 5.5-4"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

function BellIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 5a4.5 4.5 0 0 0-4.5 4.5v2.34c0 .82-.24 1.61-.7 2.29L5.5 16.1a1 1 0 0 0 .83 1.56h11.34A1 1 0 0 0 18.5 16.1l-1.3-1.97a4.1 4.1 0 0 1-.7-2.29V9.5A4.5 4.5 0 0 0 12 5Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <path
        d="M10 18.2a2.2 2.2 0 0 0 4 0"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

function ProfileIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M12 12.25a3.75 3.75 0 1 0 0-7.5 3.75 3.75 0 0 0 0 7.5Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <path
        d="M5.5 19.25a6.5 6.5 0 0 1 13 0"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

function userInitial(user: UserSummary | null) {
  const label = user?.name || user?.username || '?'
  return label.charAt(0).toUpperCase()
}

function RouteLoadingCard() {
  return <div className="page loading-page">Loading page...</div>
}

function Shell() {
  const navigate = useNavigate()
  const location = useLocation()
  const routeCourtMatch = useMatch('/courts/:courtId')
  const queryClientApi = useQueryClient()
  const activeCourtId = routeCourtMatch ? Number(routeCourtMatch.params.courtId || 0) : 0

  const [selectedState, setSelectedState] = useState('')
  const [selectedCounty, setSelectedCounty] = useState('')
  const [selectedDayKey, setSelectedDayKey] = useState<string | null>(null)
  const [scheduleComposerDayKey, setScheduleComposerDayKey] = useState<string | null>(null)
  const [mineOnly, setMineOnly] = useState(false)
  const [authOpen, setAuthOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [scheduleComposerOpen, setScheduleComposerOpen] = useState(false)
  const [scheduleDayOpen, setScheduleDayOpen] = useState(false)
  const [selectedScheduleItem, setSelectedScheduleItem] = useState<BannerItem | null>(null)
  const [nativeApiInput, setNativeApiInput] = useState(() => getConfiguredApiBaseUrl())
  const [nativePublicInput, setNativePublicInput] = useState(() => getPublicAppUrl())
  const nativeApiConfigured = !isNativePlatform() || Boolean(getConfiguredApiBaseUrl())

  useNativeKeyboardClass()

  const bootstrapQuery = useQuery({
    queryKey: ['bootstrap'],
    queryFn: () => api.get<BootstrapData>('/api/app/bootstrap'),
    enabled: nativeApiConfigured,
  })

  const bootstrap = bootstrapQuery.data
  const currentUser = bootstrap?.user || null

  const presenceQuery = useQuery({
    queryKey: ['presence-status'],
    queryFn: () => api.get<PresenceStatus>('/api/presence/status'),
    enabled: Boolean(currentUser),
    initialData: bootstrap?.presence || undefined,
  })

  const presence = currentUser
    ? presenceQuery.data || bootstrap?.presence || { checked_in: false }
    : null
  const routeSessionId = Number(new URLSearchParams(location.search).get('session') || 0) || 0

  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' })
  }, [location.pathname])

  useEffect(() => {
    if (!bootstrap) return
    if (!selectedState) {
      setSelectedState(
        bootstrap.location.selected_state_abbr
          || bootstrap.location.states[0]?.abbr
          || 'CA',
      )
    }
    if (!selectedCounty) {
      const fallbackCounty = bootstrap.location.selected_county_slug
        || bootstrap.location.default_county_slug
        || bootstrap.location.counties[0]?.slug
        || ''
      setSelectedCounty(fallbackCounty)
    }
  }, [bootstrap, selectedCounty, selectedState])

  useEffect(() => {
    if (!bootstrap || !selectedState) return
    const validCounties = bootstrap.location.counties.filter(
      (county) => county.state === selectedState && county.has_courts,
    )
    if (!validCounties.length) return
    if (!validCounties.some((county) => county.slug === selectedCounty)) {
      setSelectedCounty(validCounties[0].slug)
    }
  }, [bootstrap, selectedCounty, selectedState])

  const visibleCounties = (bootstrap?.location.counties || []).filter(
    (county) => county.state === selectedState && county.has_courts,
  )
  const selectedCountyName = bootstrap?.location.counties.find((county) => county.slug === selectedCounty)?.name || selectedCounty

  const bannerQuery = useQuery({
    queryKey: ['banner', activeCourtId || null, selectedCounty, mineOnly],
    queryFn: () => {
      const params = new URLSearchParams()
      if (activeCourtId) {
        params.set('court_id', String(activeCourtId))
      } else if (selectedCounty) {
        params.set('county_slug', selectedCounty)
      }
      if (mineOnly) {
        params.set('user_only', '1')
      }
      return api.get<ScheduleBannerData>(`/api/sessions/banner?${params.toString()}`)
    },
    enabled: Boolean(activeCourtId || selectedCounty),
    initialData: !activeCourtId && !mineOnly ? bootstrap?.schedule_banner : undefined,
  })

  const scheduleCourtsQuery = useQuery({
    queryKey: ['schedule-courts', selectedState, selectedCounty],
    queryFn: () =>
      api.get<CourtsResponse>(
        `/api/courts?state=${encodeURIComponent(selectedState)}&county_slug=${encodeURIComponent(selectedCounty)}`,
      ),
    enabled: scheduleComposerOpen && !activeCourtId && Boolean(selectedState && selectedCounty),
  })

  const notificationsQuery = useQuery({
    queryKey: ['notifications'],
    queryFn: () => api.get<{ notifications: NotificationItem[] }>('/api/auth/notifications'),
    enabled: notificationsOpen && Boolean(currentUser),
  })
  const bannerData = bannerQuery.data || bootstrap?.schedule_banner || null
  const selectedDayItems = selectedDayKey
    ? (bannerData?.items || []).filter((item) => item.start_time?.startsWith(selectedDayKey))
    : []
  const selectedScheduleSessionId = selectedScheduleItem?.item_type === 'session'
    ? selectedScheduleItem.reference_id
    : routeSessionId
  const scheduleSessionQuery = useQuery({
    queryKey: ['schedule-session', selectedScheduleSessionId],
    queryFn: () => api.get<{ session: PlaySessionData }>(`/api/sessions/${selectedScheduleSessionId}`),
    enabled: Boolean(selectedScheduleSessionId),
  })

  useEffect(() => {
    if (!routeSessionId) return
    if (selectedScheduleItem?.item_type === 'session' && selectedScheduleItem.reference_id === routeSessionId) {
      return
    }
    const matchedItem = (bannerData?.items || []).find(
      (item) => item.item_type === 'session' && item.reference_id === routeSessionId,
    )
    if (matchedItem) {
      setSelectedScheduleItem(matchedItem)
      return
    }
    setSelectedScheduleItem({
      id: `session-${routeSessionId}`,
      reference_id: routeSessionId,
      item_type: 'session',
      title: 'Scheduled session',
      subtitle: 'Court',
      court_id: activeCourtId,
      court_name: activeCourtId ? `Court ${activeCourtId}` : 'Court',
      start_time: null,
    })
  }, [activeCourtId, bannerData, routeSessionId, selectedScheduleItem])

  async function refreshPresence() {
    await Promise.all([
      queryClientApi.invalidateQueries({ queryKey: ['presence-status'] }),
      queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
      queryClientApi.invalidateQueries({ queryKey: ['banner'] }),
      queryClientApi.invalidateQueries({ queryKey: ['court-hub'] }),
    ])
  }

  async function refreshScheduleData(sessionId?: number | null) {
    const work = [
      queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
      queryClientApi.invalidateQueries({ queryKey: ['banner'] }),
      queryClientApi.invalidateQueries({ queryKey: ['court-hub'] }),
      queryClientApi.invalidateQueries({ queryKey: ['notifications'] }),
      queryClientApi.invalidateQueries({ queryKey: ['inbox'] }),
    ]
    if (sessionId) {
      work.push(queryClientApi.invalidateQueries({ queryKey: ['schedule-session', sessionId] }))
    }
    await Promise.all(work)
  }

  useEffect(() => {
    if (!currentUser || !presence?.checked_in) return undefined

    let cancelled = false
    const pingPresence = async () => {
      try {
        const nextPresence = await api.post<PresenceStatus>('/api/presence/ping', {})
        if (cancelled) return
        queryClientApi.setQueryData(['presence-status'], nextPresence)
        if (!nextPresence.checked_in) {
          await Promise.all([
            queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
            queryClientApi.invalidateQueries({ queryKey: ['banner'] }),
            queryClientApi.invalidateQueries({ queryKey: ['court-hub'] }),
          ])
        }
      } catch {
        // Ignore transient heartbeat issues and let the next tick retry.
      }
    }

    const intervalId = window.setInterval(() => {
      void pingPresence()
    }, 4 * 60_000)

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void pingPresence()
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      cancelled = true
      window.clearInterval(intervalId)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [currentUser, presence?.checked_in, queryClientApi])

  useEffect(() => {
    const socketServerUrl = getSocketServerUrl()
    if (isNativePlatform() && !socketServerUrl) {
      return undefined
    }
    const socket = socketServerUrl
      ? io(socketServerUrl, {
          path: '/socket.io',
          transports: ['websocket', 'polling'],
        })
      : io({
          path: '/socket.io',
          transports: ['websocket', 'polling'],
        })
    const token = getStoredToken()
    let joinedCourtRoom = ''
    let joinedUserRoom = ''

    function joinRooms() {
      if (currentUser && token && currentUser.id) {
        const nextUserRoom = `user_${currentUser.id}`
        if (joinedUserRoom !== nextUserRoom) {
          if (joinedUserRoom) socket.emit('leave', { room: joinedUserRoom })
          joinedUserRoom = nextUserRoom
          socket.emit('join', { room: nextUserRoom, token })
        }
      }
      if (activeCourtId && token) {
        const nextCourtRoom = `court_${activeCourtId}`
        if (joinedCourtRoom !== nextCourtRoom) {
          if (joinedCourtRoom) socket.emit('leave', { room: joinedCourtRoom })
          joinedCourtRoom = nextCourtRoom
          socket.emit('join', { room: nextCourtRoom, token })
        }
      } else if (joinedCourtRoom) {
        socket.emit('leave', { room: joinedCourtRoom })
        joinedCourtRoom = ''
      }
    }

    function invalidateShell() {
      void Promise.all([
        queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
        queryClientApi.invalidateQueries({ queryKey: ['banner'] }),
      ])
    }

    socket.on('connect', joinRooms)
    socket.on('presence_update', (payload: { court_id?: number }) => {
      if (payload.court_id && activeCourtId && payload.court_id !== activeCourtId) return
      void queryClientApi.invalidateQueries({ queryKey: ['court-hub', activeCourtId] })
      invalidateShell()
    })
    socket.on('ranked_update', (payload: { court_id?: number }) => {
      if (payload.court_id && activeCourtId && payload.court_id !== activeCourtId) return
      void queryClientApi.invalidateQueries({ queryKey: ['court-hub', activeCourtId] })
      invalidateShell()
    })
    socket.on('notification_update', () => {
      invalidateShell()
      void queryClientApi.invalidateQueries({ queryKey: ['notifications'] })
      void queryClientApi.invalidateQueries({ queryKey: ['inbox'] })
    })
    socket.on('new_message', (payload: { court_id?: number; recipient_id?: number; session_id?: number }) => {
      if (payload.court_id && activeCourtId && payload.court_id === activeCourtId) {
        void queryClientApi.invalidateQueries({ queryKey: ['court-hub', activeCourtId] })
      }
      if (payload.recipient_id && currentUser && payload.recipient_id === currentUser.id) {
        void queryClientApi.invalidateQueries({ queryKey: ['inbox'] })
        void queryClientApi.invalidateQueries({ queryKey: ['notifications'] })
      }
      if (payload.session_id) {
        void queryClientApi.invalidateQueries({ queryKey: ['inbox-thread'] })
      }
    })

    joinRooms()

    return () => {
      if (joinedCourtRoom) socket.emit('leave', { room: joinedCourtRoom })
      if (joinedUserRoom) socket.emit('leave', { room: joinedUserRoom })
      socket.disconnect()
    }
  }, [activeCourtId, currentUser, queryClientApi])

  async function handleAuthSuccess() {
    setAuthOpen(false)
    await Promise.all([
      queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
      queryClientApi.invalidateQueries({ queryKey: ['banner'] }),
      queryClientApi.invalidateQueries({ queryKey: ['court-hub'] }),
      queryClientApi.invalidateQueries({ queryKey: ['inbox'] }),
    ])
  }

  async function handleLogout() {
    clearSession()
    await Promise.all([
      queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
      queryClientApi.invalidateQueries({ queryKey: ['banner'] }),
      queryClientApi.invalidateQueries({ queryKey: ['court-hub'] }),
      queryClientApi.invalidateQueries({ queryKey: ['inbox'] }),
      queryClientApi.invalidateQueries({ queryKey: ['notifications'] }),
    ])
    navigate('/map')
  }

  async function handleMarkNotificationsRead() {
    if (!currentUser) return
    await api.post('/api/auth/notifications/read', {})
    await Promise.all([
      queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
      queryClientApi.invalidateQueries({ queryKey: ['notifications'] }),
    ])
  }

  function clearScheduleQuery() {
    const params = new URLSearchParams(location.search)
    params.delete('session')
    const nextSearch = params.toString()
    navigate(
      {
        pathname: location.pathname,
        search: nextSearch ? `?${nextSearch}` : '',
      },
      { replace: true },
    )
  }

  function openScheduleDay(dayKey: string | null) {
    setSelectedDayKey(dayKey)
    const nextSelection = resolveDaySelection(dayKey, bannerData?.items || [])
    if (nextSelection.type === 'clear') {
      setScheduleDayOpen(false)
      return
    }
    if (nextSelection.type === 'item' && nextSelection.item) {
      openScheduleItem(nextSelection.item)
      return
    }
    setScheduleDayOpen(true)
  }

  function openScheduleComposer(dayKey: string | null = selectedDayKey) {
    setScheduleComposerDayKey(dayKey || null)
    setScheduleDayOpen(false)
    setScheduleComposerOpen(true)
  }

  function openScheduleItem(item: BannerItem) {
    if (routeSessionId && (item.item_type !== 'session' || item.reference_id !== routeSessionId)) {
      clearScheduleQuery()
    }
    setSelectedScheduleItem(item)
    setScheduleDayOpen(false)
  }

  function closeScheduleItem() {
    setSelectedScheduleItem(null)
    if (routeSessionId) {
      clearScheduleQuery()
    }
  }

  async function handleNotificationSelect(notification: NotificationItem) {
    if (!currentUser) {
      setAuthOpen(true)
      return
    }

    try {
      await api.post(`/api/auth/notifications/${notification.id}/read`, {})
    } catch {
      // Ignore read failures so navigation still works.
    }

    setNotificationsOpen(false)
    await Promise.all([
      queryClientApi.invalidateQueries({ queryKey: ['bootstrap'] }),
      queryClientApi.invalidateQueries({ queryKey: ['notifications'] }),
    ])
    navigate(notification.target_path || '/map')
  }

  async function handlePresenceCheckout() {
    if (!currentUser || !presence?.checked_in) return
    try {
      await api.post('/api/presence/checkout', {})
      await refreshPresence()
      if (activeCourtId && presence.court_id === activeCourtId) {
        navigate('/map')
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to check out right now')
    }
  }

  async function handlePresenceToggleLfg() {
    if (!currentUser || !presence?.checked_in) return
    try {
      await api.post('/api/presence/lfg', {})
      await refreshPresence()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to update ready status')
    }
  }

  async function handleJoinScheduledSession(sessionId: number) {
    if (!currentUser) {
      setAuthOpen(true)
      return
    }
    try {
      await api.post(`/api/sessions/${sessionId}/join`, {})
      await refreshScheduleData(sessionId)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to join this game right now')
    }
  }

  async function handleLeaveScheduledSession(sessionId: number) {
    if (!currentUser) {
      setAuthOpen(true)
      return
    }
    try {
      await api.post(`/api/sessions/${sessionId}/leave`, {})
      await refreshScheduleData(sessionId)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to leave this game right now')
    }
  }

  async function handleCancelScheduledSession(sessionId: number) {
    if (!currentUser) {
      setAuthOpen(true)
      return
    }
    try {
      await api.delete(`/api/sessions/${sessionId}`)
      closeScheduleItem()
      await refreshScheduleData(sessionId)
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to cancel this game right now')
    }
  }

  async function handleInviteFriendsToSession(sessionId: number, friendIds: number[]) {
    if (!currentUser) {
      setAuthOpen(true)
      return
    }
    if (!friendIds.length) {
      throw new Error('Pick at least one friend to invite.')
    }

    await api.post(`/api/sessions/${sessionId}/invite`, {
      friend_ids: friendIds,
    })
    await refreshScheduleData(sessionId)
  }

  async function handleCreateScheduledSession(values: {
    courtId: number
    title: string
    startTime: string
    durationMinutes: number
    gameType: 'open' | 'singles' | 'doubles'
    visibility: 'all' | 'friends'
    maxPlayers: number
    inviteFriendIds: number[]
    recurrence: 'none' | 'weekly' | 'biweekly'
    recurrenceCount: number
  }) {
    if (!currentUser) {
      setScheduleComposerOpen(false)
      setAuthOpen(true)
      return
    }

    const startDate = new Date(values.startTime)
    if (Number.isNaN(startDate.getTime())) {
      throw new Error('Pick a valid day and time.')
    }
    const endDate = new Date(startDate.getTime() + values.durationMinutes * 60_000)

    const response = await api.post<{ session: PlaySessionData }>('/api/sessions', {
      court_id: values.courtId,
      session_type: 'scheduled',
      notes: values.title,
      game_type: values.gameType,
      visibility: values.visibility,
      max_players: values.maxPlayers,
      start_time: formatLocalDateTime(startDate),
      end_time: formatLocalDateTime(endDate),
      skill_level: 'all',
      invite_friends: values.inviteFriendIds,
      recurrence: values.recurrence,
      recurrence_count: values.recurrenceCount,
    })

    setScheduleComposerOpen(false)
    setScheduleComposerDayKey(null)
    await refreshScheduleData(response.session.id)
    setSelectedScheduleItem({
      id: `session-${response.session.id}`,
      reference_id: response.session.id,
      item_type: 'session',
      title: response.session.notes || 'Open Play',
      subtitle: response.session.court?.name || 'Court',
      court_id: response.session.court_id,
      court_name: response.session.court?.name || 'Court',
      start_time: response.session.start_time,
      end_time: response.session.end_time,
      visibility: response.session.visibility,
      game_type: response.session.game_type,
      status: response.session.status,
      is_mine: true,
      participant_count: 1 + response.session.players.filter((player) => player.status === 'joined').length,
      viewer_status: 'creator',
      creator_name: response.session.creator?.name || response.session.creator?.username,
      max_players: response.session.max_players,
      spots_taken: 1 + response.session.players.filter((player) => player.status === 'joined').length,
      spots_remaining: Math.max(
        0,
        response.session.max_players - (1 + response.session.players.filter((player) => player.status === 'joined').length),
      ),
      is_friend_only: response.session.visibility === 'friends',
    })
  }

  function handleStateChange(value: string) {
    startTransition(() => {
      setSelectedState(value)
      setSelectedDayKey(null)
      setScheduleDayOpen(false)
    })
  }

  function handleCountyChange(value: string) {
    startTransition(() => {
      setSelectedCounty(value)
      setSelectedDayKey(null)
      setScheduleDayOpen(false)
    })
  }

  function applyNativeUrlConfig(apiBaseUrl: string, publicAppUrl: string) {
    setConfiguredApiBaseUrl(apiBaseUrl)
    setPublicAppUrl(publicAppUrl || apiBaseUrl)
    window.location.reload()
  }

  if (!nativeApiConfigured) {
    return (
      <div className="app-shell">
        <div className="loading-screen">
          <BrandMark />
          <h1>Native API base URL missing</h1>
          <p>
            Set `VITE_API_BASE_URL` and `VITE_PUBLIC_APP_URL` before building the Capacitor app so
            native API, socket, and share links can resolve outside the device webview.
          </p>
          <div className="native-config-form">
            <label className="form-field">
              <span>API base URL</span>
              <input
                type="url"
                inputMode="url"
                autoCapitalize="off"
                autoCorrect="off"
                placeholder="http://127.0.0.1:5001"
                value={nativeApiInput}
                onChange={(event) => setNativeApiInput(event.target.value)}
              />
            </label>
            <label className="form-field">
              <span>Public app URL</span>
              <input
                type="url"
                inputMode="url"
                autoCapitalize="off"
                autoCorrect="off"
                placeholder="http://127.0.0.1:5001"
                value={nativePublicInput}
                onChange={(event) => setNativePublicInput(event.target.value)}
              />
            </label>
            <div className="native-config-actions">
              <button
                type="button"
                className="primary-btn"
                onClick={() => applyNativeUrlConfig(nativeApiInput, nativePublicInput)}
                disabled={!nativeApiInput.trim()}
              >
                Save and reload
              </button>
              <button
                type="button"
                className="secondary-btn"
                onClick={() => {
                  const localUrl = 'http://127.0.0.1:5001'
                  setNativeApiInput(localUrl)
                  setNativePublicInput(localUrl)
                  applyNativeUrlConfig(localUrl, localUrl)
                }}
              >
                Use local simulator
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (bootstrapQuery.isLoading || !bootstrap) {
    return (
      <div className="app-shell">
        <div className="loading-screen">
          <BrandMark />
          <h1>Loading the mobile court hub...</h1>
          <p>Pulling schedule, map context, and your live court actions together.</p>
        </div>
      </div>
    )
  }

  const notificationCount = bootstrap.unread_counts.notifications || 0
  const inboxCount = bootstrap.unread_counts.inbox || 0
  const isMapRoute = location.pathname === '/map' || location.pathname === '/'
  const scheduleOverlayOpen = scheduleComposerOpen || scheduleDayOpen || Boolean(selectedScheduleItem)
  const hidePresenceDockOnRoute = location.pathname === '/profile' || location.pathname === '/inbox'
  const showPresenceDock = Boolean(
    location.pathname !== '/map'
    && location.pathname !== '/'
    && !hidePresenceDockOnRoute
    && !scheduleOverlayOpen
    && currentUser
    && presence?.checked_in
      && presence.court_id
      && (!activeCourtId || activeCourtId !== presence.court_id),
  )

  return (
    <div className={`app-shell ${isMapRoute ? 'map-route-shell' : ''}`.trim()}>
      <header className={`top-bar ${isMapRoute ? 'map-top-bar' : ''}`.trim()}>
        <button type="button" className="brand-home-btn" aria-label="Open map" onClick={() => navigate('/map')}>
          <BrandMark />
        </button>
        <div className="header-actions">
          <button
            type="button"
            className="header-icon-btn"
            aria-label="Notifications"
            onClick={() => {
              if (!currentUser) {
                setAuthOpen(true)
                return
              }
              setNotificationsOpen(true)
            }}
          >
            <BellIcon />
            {notificationCount ? <span className="badge-dot">{notificationCount}</span> : null}
          </button>
          <button
            type="button"
            className="header-icon-btn"
            aria-label="Messages"
            onClick={() => {
              if (!currentUser) {
                setAuthOpen(true)
                return
              }
              navigate('/inbox')
            }}
          >
            <InboxIcon />
            {inboxCount ? <span className="badge-dot">{inboxCount}</span> : null}
          </button>
          <button
            type="button"
            className={`header-avatar-btn ${currentUser ? '' : 'guest'}`}
            aria-label={currentUser ? 'Profile' : 'Sign in'}
            onClick={() => {
              if (!currentUser) {
                setAuthOpen(true)
                return
              }
              navigate('/profile')
            }}
          >
            {currentUser?.photo_url ? (
              <img
                className="header-avatar-image"
                src={currentUser.photo_url}
                alt={currentUser.name || currentUser.username || 'Profile'}
              />
            ) : currentUser ? (
              <span className="header-avatar-fallback">{userInitial(currentUser)}</span>
            ) : (
              <span className="header-avatar-fallback icon">
                <ProfileIcon />
              </span>
            )}
          </button>
        </div>
      </header>

      {showPresenceDock ? (
        <section className="presence-dock">
          <div className="presence-dock-copy">
            <div className="section-kicker">Live</div>
            <strong>{presence?.court_name || 'Checked in'}</strong>
            <p>{presence?.looking_for_game ? 'Ready to play now' : 'Checked in and active'}</p>
          </div>
          <div className="presence-dock-actions">
            {activeCourtId !== presence?.court_id ? (
              <button
                type="button"
                className="chip"
                onClick={() => navigate(`/courts/${presence?.court_id}`)}
              >
                Open Court
              </button>
            ) : null}
            <button
              type="button"
              className={presence?.looking_for_game ? 'chip active' : 'chip'}
              onClick={handlePresenceToggleLfg}
            >
              {presence?.looking_for_game ? 'Ready Now' : 'Want Game'}
            </button>
            <button type="button" className="chip" onClick={handlePresenceCheckout}>
              Check Out
            </button>
          </div>
        </section>
      ) : null}

      <main className={`app-main ${isMapRoute ? 'map-route-main' : ''}`.trim()}>
        <Suspense fallback={<RouteLoadingCard />}>
          <Routes>
            <Route path="/" element={<Navigate to="/map" replace />} />
            <Route
              path="/map"
              element={
                <MapPage
                  selectedState={selectedState}
                  selectedCounty={selectedCounty}
                  currentPresence={presence}
                  onStateChange={handleStateChange}
                  onCountyChange={handleCountyChange}
                  states={bootstrap.location.states}
                  counties={visibleCounties}
                  scheduleBanner={bannerData}
                  mineOnly={mineOnly}
                  currentUser={currentUser}
                  selectedDayKey={selectedDayKey}
                  onToggleMineOnly={currentUser ? () => setMineOnly((value) => !value) : undefined}
                  onOpenScheduleDay={openScheduleDay}
                  onOpenScheduleComposer={() => openScheduleComposer(selectedDayKey)}
                />
              }
            />
            <Route
              path="/courts/:courtId"
              element={
                <CourtPage
                  currentUser={currentUser}
                  currentPresence={presence}
                  friends={bootstrap.friends}
                  onOpenScheduleComposer={(dayKey) => openScheduleComposer(dayKey)}
                  onOpenScheduleItem={openScheduleItem}
                  onPresenceRefresh={refreshPresence}
                  onRequireAuth={() => setAuthOpen(true)}
                />
              }
            />
            <Route
              path="/inbox"
              element={<InboxPage currentUser={currentUser} onRequireAuth={() => setAuthOpen(true)} />}
            />
            <Route
              path="/profile"
              element={
                <ProfilePage
                  currentUser={currentUser}
                  currentPresence={presence}
                  selectedCounty={selectedCounty}
                  selectedCountyName={selectedCountyName}
                  onRequireAuth={() => setAuthOpen(true)}
                  onLogout={handleLogout}
                  onProfileUpdated={handleAuthSuccess}
                />
              }
            />
            <Route path="*" element={<Navigate to="/map" replace />} />
          </Routes>
        </Suspense>
      </main>

      <AuthSheet open={authOpen} onClose={() => setAuthOpen(false)} onSuccess={handleAuthSuccess} />
      <NotificationsSheet
        open={notificationsOpen}
        currentUser={currentUser}
        loading={notificationsQuery.isFetching}
        notifications={notificationsQuery.data?.notifications || []}
        onClose={() => setNotificationsOpen(false)}
        onRequireAuth={() => setAuthOpen(true)}
        onMarkAllRead={handleMarkNotificationsRead}
        onSelectNotification={handleNotificationSelect}
      />
      <ScheduleComposerSheet
        open={scheduleComposerOpen}
        currentUser={currentUser}
        lockedCourtId={activeCourtId || null}
        lockedCourtName={
          activeCourtId
            ? queryClientApi.getQueryData<CourtHubData>(['court-hub', activeCourtId])?.header.court.name || `Court ${activeCourtId}`
            : null
        }
        initialDayKey={scheduleComposerDayKey}
        courtOptions={scheduleCourtsQuery.data?.courts || []}
        loadingCourts={scheduleCourtsQuery.isFetching}
        friends={bootstrap.friends}
        onClose={() => {
          setScheduleComposerOpen(false)
          setScheduleComposerDayKey(null)
        }}
        onRequireAuth={() => setAuthOpen(true)}
        onSubmit={handleCreateScheduledSession}
      />
      <ScheduleDaySheet
        open={scheduleDayOpen}
        title={formatDayTitle(selectedDayKey)}
        items={selectedDayItems}
        onClose={() => {
          setScheduleDayOpen(false)
          setSelectedDayKey(null)
        }}
        onCreate={() => {
          setScheduleDayOpen(false)
          openScheduleComposer(selectedDayKey)
        }}
        onSelectItem={openScheduleItem}
      />
      <ScheduleItemSheet
        open={Boolean(selectedScheduleItem)}
        item={selectedScheduleItem}
        session={scheduleSessionQuery.data?.session || null}
        loading={scheduleSessionQuery.isFetching}
        currentUser={currentUser}
        friends={bootstrap.friends}
        onClose={closeScheduleItem}
        onOpenCourt={(courtId) => {
          closeScheduleItem()
          navigate(`/courts/${courtId}`)
        }}
        onRequireAuth={() => setAuthOpen(true)}
        onJoinSession={handleJoinScheduledSession}
        onLeaveSession={handleLeaveScheduledSession}
        onCancelSession={handleCancelScheduledSession}
        onInviteFriends={handleInviteFriendsToSession}
      />

      {location.pathname === '/map' ? <div className="page-glow map-glow" /> : <div className="page-glow" />}
    </div>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
