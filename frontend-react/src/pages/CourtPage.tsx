import { useEffect, useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { ActionCenterCard } from '../components/ActionCenterCard'
import { BottomSheet } from '../components/BottomSheet'
import { CompetitiveHistoryPanel } from '../components/CompetitiveHistoryPanel'
import { ScheduleBannerView } from '../components/ScheduleBanner'
import { ScheduleDaySheet } from '../components/ScheduleDaySheet'
import { api } from '../lib/api'
import { openExternalUrl, shareLink } from '../lib/native'
import { buildPublicAppUrl } from '../lib/runtime'
import { resolveDaySelection } from '../lib/schedule'
import type {
  ActionButtonPayload,
  BannerItem,
  CourtHubData,
  MatchPlayerSummary,
  PresenceStatus,
  QueueReadyCohort,
  UserSummary,
} from '../types'

interface CourtPageProps {
  currentUser: UserSummary | null
  currentPresence: PresenceStatus | null
  friends: UserSummary[]
  onOpenScheduleComposer: (dayKey?: string | null) => void
  onOpenScheduleItem: (item: BannerItem) => void
  onPresenceRefresh: () => Promise<void> | void
  onRequireAuth: () => void
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return 'TBD'
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatDayTitle(value: string | null | undefined) {
  if (!value) return 'Schedule'
  return new Date(`${value}T00:00:00`).toLocaleDateString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

function formatDateTimeInputValue(date: Date) {
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function buildQuickRankedSlots() {
  const now = new Date()
  const evening = new Date(now)
  evening.setSeconds(0, 0)
  evening.setHours(18, 0, 0, 0)
  if (now >= evening) {
    evening.setDate(evening.getDate() + 1)
  }

  const tomorrowEvening = new Date(now)
  tomorrowEvening.setDate(tomorrowEvening.getDate() + 1)
  tomorrowEvening.setHours(18, 0, 0, 0)

  const weekendMorning = new Date(now)
  weekendMorning.setDate(weekendMorning.getDate() + ((6 - weekendMorning.getDay() + 7) % 7 || 7))
  weekendMorning.setHours(10, 0, 0, 0)

  const rawSlots = [
    {
      label: evening.toDateString() === now.toDateString() ? 'Tonight 6 PM' : 'Next 6 PM',
      value: formatDateTimeInputValue(evening),
    },
    {
      label: 'Tomorrow 6 PM',
      value: formatDateTimeInputValue(tomorrowEvening),
    },
    {
      label: `${weekendMorning.toLocaleDateString([], { weekday: 'short' })} 10 AM`,
      value: formatDateTimeInputValue(weekendMorning),
    },
  ]

  return rawSlots.filter((slot, index, slots) => slots.findIndex((entry) => entry.value === slot.value) === index)
}

function teamSummary(players: MatchPlayerSummary[] | undefined, fallback: string) {
  if (!players?.length) return fallback
  return players
    .map((player) => player.user?.name || player.user?.username || 'Player')
    .join(' / ')
}

function ShareIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M14.5 5.5a2.5 2.5 0 1 0 2.16 3.75l-8.01 4.12a2.5 2.5 0 1 0 0 1.26l8.01 4.12a2.5 2.5 0 1 0 .58-1.16l-8.01-4.12a2.55 2.55 0 0 0 0-.94l8.01-4.12A2.5 2.5 0 0 0 14.5 5.5Z"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

function DirectionsIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M13.25 4.75 18.5 10l-5.25 5.25"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
      <path
        d="M18 10H9.5a4.75 4.75 0 0 0 0 9.5H11"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className={open ? 'open' : ''}>
      <path
        d="m5.75 7.5 4.25 4.5 4.25-4.5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

function formatTipLabel(value: string) {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

function Accordion({
  title,
  children,
  eyebrow,
  summary,
  badge,
  defaultOpen = false,
  className = '',
}: {
  title: string
  children: ReactNode
  eyebrow?: string
  summary?: string
  badge?: string
  defaultOpen?: boolean
  className?: string
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className={`accordion-card ${open ? 'open' : ''} ${className}`.trim()}>
      <button type="button" className="accordion-toggle" onClick={() => setOpen((value) => !value)}>
        <div className="accordion-heading">
          {eyebrow ? <div className="section-kicker accordion-kicker">{eyebrow}</div> : null}
          <div className="accordion-copy">
            <strong>{title}</strong>
            {summary ? <span>{summary}</span> : null}
          </div>
        </div>
        <div className="accordion-tail">
          {badge ? <span className="queue-pill accordion-pill">{badge}</span> : null}
          <span className="accordion-chevron">
            <ChevronIcon open={open} />
          </span>
        </div>
      </button>
      {open ? <div className="accordion-body">{children}</div> : null}
    </section>
  )
}

export function CourtPage({
  currentUser,
  currentPresence,
  friends,
  onOpenScheduleComposer,
  onOpenScheduleItem,
  onPresenceRefresh,
  onRequireAuth,
}: CourtPageProps) {
  const params = useParams()
  const queryClient = useQueryClient()
  const [scoreSheetOpen, setScoreSheetOpen] = useState(false)
  const [scoreMatchId, setScoreMatchId] = useState<number | null>(null)
  const [scoreValues, setScoreValues] = useState({ team1: '', team2: '' })
  const [rankedSheetOpen, setRankedSheetOpen] = useState(false)
  const [rankedOpponentId, setRankedOpponentId] = useState('')
  const [rankedTime, setRankedTime] = useState('')
  const [chatMessage, setChatMessage] = useState('')
  const [showAllChat, setShowAllChat] = useState(false)
  const [scheduleDayKey, setScheduleDayKey] = useState<string | null>(null)
  const [scheduleSheetOpen, setScheduleSheetOpen] = useState(false)
  const [heroImageFailed, setHeroImageFailed] = useState(false)

  const courtId = Number(params.courtId || 0)
  const hubQuery = useQuery({
    queryKey: ['court-hub', courtId],
    queryFn: () => api.get<CourtHubData>(`/api/courts/${courtId}/hub`),
    enabled: Number.isFinite(courtId) && courtId > 0,
  })

  const hub = hubQuery.data

  useEffect(() => {
    setHeroImageFailed(false)
  }, [courtId, hub?.details.court.photo_url, hub?.header.court.photo_url, hub?.details.images])

  async function refreshCourt() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['court-hub', courtId] }),
      queryClient.invalidateQueries({ queryKey: ['banner'] }),
      queryClient.invalidateQueries({ queryKey: ['bootstrap'] }),
    ])
  }

  async function handleAction(
    action: ActionButtonPayload,
    extra?: { matchType?: 'singles' | 'doubles'; cohort?: QueueReadyCohort },
  ) {
    if (!currentUser && action.kind !== 'view') {
      onRequireAuth()
      return
    }

    try {
      let presenceChanged = false
      switch (action.kind) {
        case 'check_in':
          await api.post('/api/presence/checkin', { court_id: courtId })
          presenceChanged = true
          break
        case 'join_queue':
          await api.post('/api/ranked/queue/join', {
            court_id: courtId,
            match_type: extra?.matchType || 'doubles',
          })
          break
        case 'leave_queue':
          await api.post('/api/ranked/queue/leave', { court_id: courtId })
          break
        case 'challenge_player':
          if (!currentUser || !action.target_user_id) return
          await api.post('/api/ranked/challenge/court', {
            court_id: courtId,
            match_type: 'singles',
            team1: [currentUser.id],
            team2: [action.target_user_id],
          })
          break
        case 'accept_invite':
          await api.post(`/api/ranked/lobby/${action.lobby_id}/respond`, { action: 'accept' })
          break
        case 'decline_invite':
          await api.post(`/api/ranked/lobby/${action.lobby_id}/respond`, { action: 'decline' })
          break
        case 'start_lobby':
          await api.post(`/api/ranked/lobby/${action.lobby_id}/start`, {})
          break
        case 'start_queue_game':
          if (!extra?.cohort) return
          await api.post('/api/ranked/lobby/queue', {
            court_id: courtId,
            match_type: extra.cohort.match_type,
            team1: extra.cohort.team1_user_ids,
            team2: extra.cohort.team2_user_ids,
            start_immediately: true,
          })
          break
        case 'enter_score':
          if (!action.match_id) return
          setScoreMatchId(action.match_id)
          setScoreValues({ team1: '', team2: '' })
          setScoreSheetOpen(true)
          return
        case 'confirm_score':
          await api.post(`/api/ranked/match/${action.match_id}/confirm`, {})
          break
        case 'reject_score':
          await api.post(`/api/ranked/match/${action.match_id}/reject`, {})
          break
        case 'cancel_match':
          await api.post(`/api/ranked/match/${action.match_id}/cancel`, {})
          break
        case 'schedule_ranked':
          setRankedSheetOpen(true)
          return
        default:
          return
      }
      await refreshCourt()
      if (presenceChanged) {
        await onPresenceRefresh()
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Action failed')
    }
  }

  async function toggleLookingForGame() {
    try {
      await api.post('/api/presence/lfg', {})
      await refreshCourt()
      await onPresenceRefresh()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to update ready status')
    }
  }

  async function checkOut() {
    try {
      await api.post('/api/presence/checkout', {})
      await refreshCourt()
      await onPresenceRefresh()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Unable to check out right now')
    }
  }

  async function submitScore() {
    if (!scoreMatchId) return
    try {
      await api.post(`/api/ranked/match/${scoreMatchId}/score`, {
        team1_score: Number(scoreValues.team1),
        team2_score: Number(scoreValues.team2),
      })
      setScoreSheetOpen(false)
      await refreshCourt()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Could not submit score')
    }
  }

  async function submitRankedSchedule() {
    if (!currentUser) {
      onRequireAuth()
      return
    }
    try {
      await api.post('/api/ranked/challenge/scheduled', {
        court_id: courtId,
        match_type: 'singles',
        team1: [currentUser.id],
        team2: [Number(rankedOpponentId)],
        scheduled_for: rankedTime,
        source: 'friends_challenge',
      })
      setRankedSheetOpen(false)
      setRankedOpponentId('')
      setRankedTime('')
      await refreshCourt()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Could not schedule ranked play')
    }
  }

  async function sendChat() {
    if (!chatMessage.trim()) return
    if (!currentUser) {
      onRequireAuth()
      return
    }
    try {
      await api.post('/api/chat/send', {
        court_id: courtId,
        msg_type: 'court',
        content: chatMessage.trim(),
      })
      setChatMessage('')
      await refreshCourt()
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Could not send chat')
    }
  }

  async function shareCourt() {
    const shareUrl = buildPublicAppUrl(`/courts/${courtId}`)
    try {
      await shareLink({
        title: hub?.header.court.name || 'Third Shot Court',
        text: `Join me at ${hub?.header.court.name || 'this Third Shot court'}`,
        ...(shareUrl ? { url: shareUrl } : {}),
      })
      return
    } catch (error) {
      if (error instanceof Error && error.message.toLowerCase().includes('cancel')) {
        return
      }
    }
    window.alert('Unable to share this court right now.')
  }

  if (hubQuery.isLoading) {
    return <div className="page loading-page">Loading court...</div>
  }

  if (!hub) {
    return <div className="page error-page">Unable to load this court.</div>
  }

  const visibleChatMessages = showAllChat ? hub.chat_preview.messages : hub.chat_preview.messages.slice(0, 2)
  const selectedScheduleItems = scheduleDayKey
    ? hub.schedule_banner.items.filter((item) => item.start_time?.startsWith(scheduleDayKey))
    : []
  const detailTips = Object.entries(hub.details.community_info || {}).filter(([, value]) => Boolean(value))
  const statusPlayers = [
    ...hub.live_snapshot.friend_presence,
    ...hub.live_snapshot.checked_in_players.filter(
      (player) => !hub.live_snapshot.friend_presence.some((friend) => friend.id === player.id),
    ),
  ]
  const readyPlayerCount = hub.ranked.challengeable_players.length
  const firstDetailImage = hub.details.images.find(
    (image): image is { image_url: string } => typeof image.image_url === 'string' && Boolean(image.image_url),
  )
  const courtImageUrl = heroImageFailed
    ? ''
    : hub.details.court.photo_url || hub.header.court.photo_url || firstDetailImage?.image_url || ''
  const heroMetaLine = [
    hub.header.address_line,
    hub.details.court.num_courts ? `${hub.details.court.num_courts} courts` : null,
    hub.details.court.indoor ? 'Indoor' : 'Outdoor',
    hub.details.court.lighted ? 'Lighted' : null,
  ].filter(Boolean).join(' · ')
  const heroPills = [
    hub.details.court.num_courts ? `${hub.details.court.num_courts} courts` : null,
    hub.details.court.indoor ? 'Indoor' : 'Outdoor',
    hub.details.court.lighted ? 'Lighted' : null,
    hub.details.court.fees ? String(hub.details.court.fees) : 'Free play',
  ].filter(Boolean)
  const checkedInHere = Boolean(currentPresence?.checked_in && currentPresence.court_id === courtId)
  const checkedInElsewhere = Boolean(
    currentPresence?.checked_in && currentPresence.court_id && currentPresence.court_id !== courtId,
  )
  const challengeablePlayers = hub.ranked.challengeable_players.slice(0, 6)
  const queuePreviewEntries = hub.ranked.queue.slice(0, 6)
  const rosterPlayers = challengeablePlayers.length ? challengeablePlayers : statusPlayers.slice(0, 6)
  const quickRankedSlots = buildQuickRankedSlots()
  const selectedRankedFriend = friends.find((friend) => String(friend.id) === rankedOpponentId) || null
  const scoreMatch = hub.action_center.match && hub.action_center.match.id === scoreMatchId ? hub.action_center.match : null
  const teamOneLabel = teamSummary(scoreMatch?.team1, 'Team 1')
  const teamTwoLabel = teamSummary(scoreMatch?.team2, 'Team 2')
  const canSubmitScore = scoreValues.team1 !== '' && scoreValues.team2 !== ''
  const canSubmitRanked = Boolean(rankedOpponentId && rankedTime)
  const scoreWinnerLabel = canSubmitScore
    ? Number(scoreValues.team1) === Number(scoreValues.team2)
      ? 'Enter a winning score'
      : Number(scoreValues.team1) > Number(scoreValues.team2)
        ? `${teamOneLabel} ahead`
        : `${teamTwoLabel} ahead`
    : 'First to 11, win by 2'
  const playStatPills = [
    { label: 'checked in', value: hub.live_snapshot.checked_in_count },
    { label: 'ready', value: hub.live_snapshot.looking_to_play_count },
    { label: 'in queue', value: hub.ranked.queue.length },
    { label: 'live', value: hub.live_snapshot.active_match_count },
  ]
  const playHeadline = checkedInHere
    ? 'Play competitive games here'
    : checkedInElsewhere
      ? `Move from ${currentPresence?.court_name || 'your current court'}`
      : 'Check in and play now'
  const playSummary = checkedInHere
    ? `${hub.live_snapshot.looking_to_play_count} ready now · ${hub.ranked.queue.length} in queue`
    : checkedInElsewhere
      ? `You are live at ${currentPresence?.court_name || 'another court'} · move here in one tap`
      : `${hub.live_snapshot.looking_to_play_count} looking to play · ${readyPlayerCount} challengeable`
  const competitiveSummary = [
    hub.competitive_history.leaderboard.length
      ? `${hub.competitive_history.leaderboard.length} ladder spots`
      : null,
    hub.competitive_history.recent_matches.length
      ? `${hub.competitive_history.recent_matches.length} recent`
      : 'No saved matches yet',
  ].filter(Boolean).join(' · ')
  const detailsSummary = [
    heroMetaLine || null,
    hub.details.amenities.length ? `${hub.details.amenities.length} amenities` : null,
  ].filter(Boolean).join(' · ')
  const chatSummary = hub.chat_preview.messages.length
    ? `${hub.chat_preview.messages.length} recent message${hub.chat_preview.messages.length === 1 ? '' : 's'}`
    : hub.chat_preview.can_chat
      ? 'Start the first court message'
      : 'Sign in to chat here'

  function openScheduleDay(dayKey: string) {
    setScheduleDayKey(dayKey)
    const nextSelection = resolveDaySelection(dayKey, hub?.schedule_banner.items || [])
    if (nextSelection.type === 'item' && nextSelection.item) {
      onOpenScheduleItem(nextSelection.item)
      setScheduleSheetOpen(false)
      return
    }
    setScheduleSheetOpen(true)
  }

  return (
    <div className="page court-page">
      <section className="court-media-card">
        <div className="court-media-hero">
          {courtImageUrl ? (
            <img
              src={courtImageUrl}
              alt={hub.header.court.name}
              className="court-media-image"
              onError={() => setHeroImageFailed(true)}
            />
          ) : (
            <div className="court-media-fallback">
              <div className="section-kicker">Court</div>
              <strong>{hub.header.court.name}</strong>
            </div>
          )}
          <div className="court-media-overlay">
            <div className="court-media-topline">
              <div className="court-title-stack">
                <div className="section-kicker">Court</div>
                <h1>{hub.header.court.name}</h1>
              </div>
              <div className="court-media-actions">
                <button type="button" className="court-icon-chip" aria-label="Share court" onClick={shareCourt}>
                  <ShareIcon />
                </button>
                <button
                  type="button"
                  className="court-icon-chip"
                  aria-label="Get directions"
                  onClick={() =>
                    openExternalUrl(
                      `https://www.google.com/maps/search/?api=1&query=${hub.header.court.latitude},${hub.header.court.longitude}`,
                    )
                  }
                >
                  <DirectionsIcon />
                </button>
              </div>
            </div>
            <p className="court-media-meta">{heroMetaLine}</p>
            <div className="court-media-pills">
              {heroPills.map((pill) => (
                <span key={pill} className="queue-pill">
                  {pill}
                </span>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="court-activity-stack">
        <ScheduleBannerView
          data={hub.schedule_banner}
          compact
          className="court-schedule-banner"
          expanded
          selectedDayKey={scheduleDayKey}
          onToggle={() => undefined}
          onSelectDay={(dayKey) => {
            if (!dayKey) {
              setScheduleDayKey(null)
              setScheduleSheetOpen(false)
              return
            }
            openScheduleDay(dayKey)
          }}
          onCreate={onOpenScheduleComposer}
        />

        <section className="court-play-card">
          <div className="court-play-head">
            <div className="court-play-copy">
              <div className="section-kicker">Play Now</div>
              <strong>{playHeadline}</strong>
              <span>{playSummary}</span>
            </div>
            <span className={`queue-pill ${checkedInHere ? 'success-pill' : ''}`}>
              {checkedInHere ? 'Checked in' : checkedInElsewhere ? 'Checked in elsewhere' : 'Not checked in'}
            </span>
          </div>

          <div className="court-play-pills">
            {playStatPills.map((pill) => (
              <span key={pill.label} className="queue-pill">
                <strong>{pill.value}</strong>
                <em>{pill.label}</em>
              </span>
            ))}
          </div>

          <div className="court-play-actions">
            {checkedInHere ? (
              <>
                <button
                  type="button"
                  className={currentPresence?.looking_for_game ? 'chip active' : 'chip'}
                  onClick={toggleLookingForGame}
                >
                  {currentPresence?.looking_for_game ? 'Ready Now' : 'Want Game'}
                </button>
                <button type="button" className="chip" onClick={checkOut}>
                  Check Out
                </button>
              </>
            ) : (
              <button
                type="button"
                className="primary-btn compact-btn"
                onClick={() =>
                  handleAction({
                    kind: 'check_in',
                    label: checkedInElsewhere ? 'Move Here' : 'Check In',
                  })
                }
              >
                {checkedInElsewhere ? 'Move Here' : 'Check In'}
              </button>
            )}
            {!checkedInHere ? (
              <button type="button" className="secondary-btn compact-btn" onClick={() => setRankedSheetOpen(true)}>
                Schedule Later
              </button>
            ) : null}
          </div>

          <div className="court-action-shell">
            {hub.action_center.type === 'check_in' ? (
              <div className="inline-note compact">
                Queue, challenge, score, and invite actions unlock here after check-in.
              </div>
            ) : (
              <ActionCenterCard
                compact
                embedded
                minimal
                actionCenter={hub.action_center}
                queueCount={hub.ranked.queue.length}
                onAction={handleAction}
              />
            )}
          </div>

          <div className="court-lane">
            <div className="court-lane-head">
              <strong>{challengeablePlayers.length ? 'Challenge checked-in players' : 'Checked in now'}</strong>
              <span>{challengeablePlayers.length ? 'Friends first' : `${statusPlayers.length} players here`}</span>
            </div>
            {rosterPlayers.length ? (
              <div className="presence-strip tight-scroll">
                {rosterPlayers.map((player) => {
                  const canQuickChallenge = checkedInHere && Boolean(player.can_challenge)

                  if (canQuickChallenge) {
                    return (
                      <button
                        key={player.id}
                        type="button"
                        className={`player-chip compact-inline ${player.is_friend ? 'friend' : ''}`}
                        onClick={() =>
                          handleAction({
                            kind: 'challenge_player',
                            label: `Challenge ${player.name || player.username}`,
                            target_user_id: player.id,
                          })
                        }
                      >
                        <span className="player-chip-name">{player.name || player.username}</span>
                        <span className="player-chip-meta">
                          {player.is_friend ? 'Friend' : 'Player'}
                          {player.looking_for_game ? ' · Ready' : ''}
                        </span>
                      </button>
                    )
                  }

                  return (
                    <span key={player.id} className={`player-chip static compact ${player.is_friend ? 'friend' : ''}`}>
                      {player.name || player.username}
                    </span>
                  )
                })}
                {statusPlayers.length > rosterPlayers.length ? (
                  <span className="queue-pill">+{statusPlayers.length - rosterPlayers.length} more</span>
                ) : null}
              </div>
            ) : (
              <div className="queue-pill">No one checked in yet</div>
            )}
          </div>

          <div className="court-lane">
            <div className="court-lane-head">
              <strong>Ranked queue</strong>
              <span>
                {hub.ranked.queue_ready_cohort
                  ? `${hub.ranked.queue_ready_cohort.match_type} game ready`
                  : hub.ranked.queue.length
                    ? `${hub.ranked.queue.length} waiting`
                    : 'Open for the next game'}
              </span>
            </div>
            {queuePreviewEntries.length ? (
              <div className="presence-strip tight-scroll">
                {queuePreviewEntries.map((entry) => (
                  <span
                    key={entry.id}
                    className={`player-chip static compact queue-entry-chip ${entry.user.is_friend ? 'friend' : ''}`}
                  >
                    {entry.user.name || entry.user.username}
                    <em>{entry.match_type === 'singles' ? 'S' : 'D'}#{entry.match_type_position || entry.position || 1}</em>
                  </span>
                ))}
              </div>
            ) : (
              <div className="queue-pill">
                {hub.live_snapshot.active_match_count
                  ? `${hub.live_snapshot.active_match_count} live game${hub.live_snapshot.active_match_count > 1 ? 's' : ''}`
                  : 'Queue is open'}
              </div>
            )}
          </div>
        </section>
      </section>

      <Accordion
        title="Competitive History"
        eyebrow="Ranked"
        summary={competitiveSummary}
        badge={hub.competitive_history.leaderboard.length ? 'Live ladder' : 'Open ladder'}
        className="competitive-accordion"
      >
        <CompetitiveHistoryPanel
          compact
          scopeLabel={`${hub.header.court.name} ladder`}
          leaderboard={hub.competitive_history.leaderboard}
          matches={hub.competitive_history.recent_matches}
          currentUserId={currentUser?.id}
        />
      </Accordion>

      <Accordion
        title="Court Details"
        eyebrow="Local"
        summary={detailsSummary || 'Court facts and local tips'}
        badge={hub.details.amenities.length ? `${hub.details.amenities.length} perks` : 'Court info'}
        className="details-accordion"
      >
        {hub.details.description ? (
          <div className="detail-hero-note">
            <strong>At a glance</strong>
            <p>{hub.details.description}</p>
          </div>
        ) : null}
        <div className="details-compact-shell">
          <div className="detail-grid compact-detail-grid court-detail-grid">
            <div><span>Type</span><strong>{String(hub.details.court.court_type || 'Not listed')}</strong></div>
            <div><span>Surface</span><strong>{String(hub.details.court.surface_type || 'Not listed')}</strong></div>
            <div><span>Fees</span><strong>{String(hub.details.court.fees || 'Not listed')}</strong></div>
            <div><span>Courts</span><strong>{String(hub.details.court.num_courts || 0)}</strong></div>
          </div>
          {hub.details.amenities.length ? (
            <div className="detail-chip-cloud compact-cloud">
              {hub.details.amenities.map((amenity) => (
                <span key={amenity} className="detail-chip">{amenity}</span>
              ))}
            </div>
          ) : null}
        </div>
        <div className="detail-subsection">
          <div className="detail-subsection-head">
            <div className="section-kicker">Local Tips</div>
            <span className="queue-pill">{detailTips.length ? `${detailTips.length} notes` : 'No notes'}</span>
          </div>
          {detailTips.length ? (
            detailTips.map(([key, value]) =>
              value ? (
                <div key={key} className="detail-row">
                  <strong>{formatTipLabel(key)}</strong>
                  <span>{String(value)}</span>
                </div>
              ) : null,
            )
          ) : (
            <div className="inline-note compact">No local tips posted for this court yet.</div>
          )}
        </div>
      </Accordion>

      <Accordion
        title="Court Chat"
        eyebrow="Community"
        summary={chatSummary}
        badge={hub.chat_preview.can_chat ? 'Open chat' : 'Sign in'}
        className="chat-accordion"
      >
        <div className="chat-panel-shell">
          {visibleChatMessages.length ? (
            <div className="chat-list compact-chat-list">
            {visibleChatMessages.map((message) => (
              <article key={message.id} className="chat-bubble">
                <strong>{message.sender?.name || message.sender?.username || 'Player'}</strong>
                <p>{message.content}</p>
                <span>{formatDateTime(message.created_at)}</span>
              </article>
            ))}
            {hub.chat_preview.messages.length > 3 ? (
              <button type="button" className="secondary-btn compact-btn full-width" onClick={() => setShowAllChat((value) => !value)}>
                {showAllChat ? 'Show Less' : 'View More'}
              </button>
            ) : null}
            </div>
          ) : (
            <div className="empty-card compact-empty">
              {hub.chat_preview.can_chat
                ? 'No messages yet. Be the first to check in with the group.'
                : 'Sign in to join the court chat.'}
            </div>
          )}
          <div className="chat-composer compact-chat-composer">
            <input
              type="text"
              value={chatMessage}
              onChange={(event) => setChatMessage(event.target.value)}
              placeholder={hub.chat_preview.can_chat ? 'Message the court...' : 'Sign in to message this court'}
              disabled={!hub.chat_preview.can_chat}
            />
            <button type="button" className="primary-btn compact-btn" onClick={sendChat} disabled={!hub.chat_preview.can_chat}>
              Send
            </button>
          </div>
        </div>
      </Accordion>

      <BottomSheet
        open={scoreSheetOpen}
        title="Post Final Score"
        eyebrow="Ranked Match"
        subtitle={scoreMatch ? `${scoreMatch.match_type} • ${hub.header.court.name}` : 'Save result to ladder and profiles'}
        onClose={() => setScoreSheetOpen(false)}
        footer={
          <button type="button" className="primary-btn full-width" onClick={submitScore} disabled={!canSubmitScore}>
            Save Score
          </button>
        }
        variant="action"
      >
        <div className="action-sheet-stack">
          <div className="action-summary-card">
            <strong>{teamOneLabel} vs {teamTwoLabel}</strong>
            <span>{scoreWinnerLabel}</span>
          </div>
          <div className="score-grid">
            <label className="score-card">
              <span>{teamOneLabel}</span>
              <input
                type="number"
                inputMode="numeric"
                min="0"
                aria-label="Team 1 score"
                value={scoreValues.team1}
                onChange={(event) => setScoreValues((current) => ({ ...current, team1: event.target.value }))}
                placeholder="11"
              />
            </label>
            <div className="score-vs">vs</div>
            <label className="score-card">
              <span>{teamTwoLabel}</span>
              <input
                type="number"
                inputMode="numeric"
                min="0"
                aria-label="Team 2 score"
                value={scoreValues.team2}
                onChange={(event) => setScoreValues((current) => ({ ...current, team2: event.target.value }))}
                placeholder="9"
              />
            </label>
          </div>
        </div>
      </BottomSheet>

      <BottomSheet
        open={rankedSheetOpen}
        title="Schedule Ranked Invite"
        eyebrow="Ranked Invite"
        subtitle={selectedRankedFriend ? `${selectedRankedFriend.name || selectedRankedFriend.username} • ${hub.header.court.name}` : 'Pick a friend and lock a time'}
        onClose={() => setRankedSheetOpen(false)}
        footer={
          <button type="button" className="primary-btn full-width" onClick={submitRankedSchedule} disabled={!canSubmitRanked}>
            Send Invite
          </button>
        }
        variant="action"
      >
        <div className="action-sheet-stack">
          <div className="action-summary-card">
            <strong>{selectedRankedFriend ? `Challenge ${selectedRankedFriend.name || selectedRankedFriend.username}` : 'Choose who to challenge'}</strong>
            <span>{rankedTime ? formatDateTime(rankedTime) : 'Select a start time to send the invite'}</span>
          </div>
          <label className="form-field">
            <span>Friend</span>
            <select value={rankedOpponentId} onChange={(event) => setRankedOpponentId(event.target.value)}>
              <option value="">Select a friend</option>
              {friends.map((friend) => (
                <option key={friend.id} value={friend.id}>
                  {friend.name || friend.username}
                </option>
              ))}
            </select>
          </label>
          <div className="quick-slot-row">
            {quickRankedSlots.map((slot) => (
              <button
                key={slot.value}
                type="button"
                className={rankedTime === slot.value ? 'chip active' : 'chip'}
                onClick={() => setRankedTime(slot.value)}
              >
                {slot.label}
              </button>
            ))}
          </div>
          <label className="form-field">
            <span>Start time</span>
            <input
              type="datetime-local"
              value={rankedTime}
              onChange={(event) => setRankedTime(event.target.value)}
            />
          </label>
        </div>
      </BottomSheet>

      <ScheduleDaySheet
        open={scheduleSheetOpen}
        title={formatDayTitle(scheduleDayKey)}
        onClose={() => {
          setScheduleSheetOpen(false)
          setScheduleDayKey(null)
        }}
        items={selectedScheduleItems}
        onCreate={() => {
          setScheduleSheetOpen(false)
          onOpenScheduleComposer(scheduleDayKey)
        }}
        onSelectItem={(item) => {
          setScheduleSheetOpen(false)
          onOpenScheduleItem(item)
        }}
      />
    </div>
  )
}
