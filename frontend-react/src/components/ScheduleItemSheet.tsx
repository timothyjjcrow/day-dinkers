import { useEffect, useState } from 'react'

import { BottomSheet } from './BottomSheet'
import type { BannerItem, PlaySessionData, UserSummary } from '../types'

function formatDateTime(value: string | null | undefined) {
  if (!value) return 'Time TBD'
  return new Date(value).toLocaleString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function itemLabel(item: BannerItem | null) {
  if (!item) return 'Schedule item'
  if (item.item_type === 'ranked_lobby') return 'Ranked game'
  if (item.item_type === 'tournament') return 'Tournament'
  return 'Scheduled session'
}

function itemStatusLabel(status: string | null | undefined) {
  const normalized = String(status || '').trim().toLowerCase()
  if (!normalized) return ''
  if (normalized === 'active') return 'Live'
  if (normalized === 'cancelled') return 'Cancelled'
  if (normalized === 'completed') return 'Completed'
  return normalized.replace(/_/g, ' ')
}

function formatGameType(value: string | null | undefined) {
  const normalized = String(value || 'open').replace(/_/g, ' ').trim()
  if (!normalized) return 'Open play'
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function formatVisibility(value: string | null | undefined) {
  return value === 'friends' ? 'Friends only' : 'Open join'
}

function formatVisibilityPill(value: string | null | undefined) {
  return value === 'friends' ? 'Friends' : 'Open'
}

function joinedCount(session: PlaySessionData | null) {
  if (!session) return 0
  return 1 + session.players.filter((player) => player.status === 'joined').length
}

function sessionViewerStatus(session: PlaySessionData | null, currentUser: UserSummary | null) {
  if (!session || !currentUser) return 'none'
  if (session.creator_id === currentUser.id) return 'creator'
  return session.players.find((player) => player.user_id === currentUser.id)?.status || 'none'
}

function scheduleStateCopy({
  currentUser,
  isCreator,
  isJoined,
  isInvited,
  isWaitlisted,
  isActiveSession,
  sessionIsFull,
}: {
  currentUser: UserSummary | null
  isCreator: boolean
  isJoined: boolean
  isInvited: boolean
  isWaitlisted: boolean
  isActiveSession: boolean
  sessionIsFull: boolean
}) {
  if (!isActiveSession) {
    return {
      badge: 'Closed',
      headline: 'This game is no longer active',
      detail: 'Open the court to see what is happening there now.',
      tone: 'muted',
    }
  }
  if (!currentUser) {
    return {
      badge: 'Sign in',
      headline: 'Claim a spot in a few taps',
      detail: 'Sign in to join or manage it.',
      tone: 'accent',
    }
  }
  if (isCreator) {
    return {
      badge: 'Hosting',
      headline: 'You are hosting this game',
      detail: 'Players join here. Start from the court hub.',
      tone: 'success',
    }
  }
  if (isJoined) {
    return {
      badge: 'Confirmed',
      headline: 'You are in for this game',
      detail: 'Open the court when you are ready.',
      tone: 'success',
    }
  }
  if (isWaitlisted) {
    return {
      badge: 'Waitlist',
      headline: 'You are waiting on an open spot',
      detail: 'You will be notified if a spot opens.',
      tone: 'warning',
    }
  }
  if (isInvited) {
    return {
      badge: 'Invite',
      headline: sessionIsFull ? 'Accept the invite and join the waitlist' : 'Your invite is ready',
      detail: sessionIsFull
        ? 'It is full right now, but accepting keeps you next in line.'
        : 'Accept in one tap to lock in your spot.',
      tone: 'accent',
    }
  }
  if (sessionIsFull) {
    return {
      badge: 'Full',
      headline: 'This game is full',
      detail: 'Join the waitlist if a spot opens.',
      tone: 'warning',
    }
  }
  return {
    badge: 'Open spot',
    headline: 'This game is joinable right now',
    detail: 'Join in one tap, then head to the court when it is time.',
    tone: 'accent',
  }
}

interface ScheduleItemSheetProps {
  open: boolean
  item: BannerItem | null
  session: PlaySessionData | null
  loading?: boolean
  currentUser: UserSummary | null
  friends?: UserSummary[]
  onClose: () => void
  onOpenCourt: (courtId: number) => void
  onRequireAuth: () => void
  onJoinSession: (sessionId: number) => Promise<void> | void
  onLeaveSession: (sessionId: number) => Promise<void> | void
  onCancelSession: (sessionId: number) => Promise<void> | void
  onInviteFriends?: (sessionId: number, friendIds: number[]) => Promise<void> | void
}

export function ScheduleItemSheet({
  open,
  item,
  session,
  loading = false,
  currentUser,
  friends = [],
  onClose,
  onOpenCourt,
  onRequireAuth,
  onJoinSession,
  onLeaveSession,
  onCancelSession,
  onInviteFriends,
}: ScheduleItemSheetProps) {
  const [inviteOpen, setInviteOpen] = useState(false)
  const [inviteSearch, setInviteSearch] = useState('')
  const [invitePending, setInvitePending] = useState(false)
  const [inviteError, setInviteError] = useState('')
  const [inviteFriendIds, setInviteFriendIds] = useState<number[]>([])
  const isSession = item?.item_type === 'session'
  const resolvedSession = isSession ? session : null
  const viewerStatus = sessionViewerStatus(resolvedSession, currentUser)
  const sessionStatus = resolvedSession?.status || item?.status || 'active'
  const isCreator = viewerStatus === 'creator'
  const isJoined = viewerStatus === 'joined'
  const isInvited = viewerStatus === 'invited'
  const isWaitlisted = viewerStatus === 'waitlisted'
  const isActiveSession = sessionStatus === 'active'
  const joinedPlayers = resolvedSession?.players.filter((player) => player.status === 'joined') || []
  const invitedPlayers = resolvedSession?.players.filter((player) => player.status === 'invited') || []
  const waitlistedPlayers = resolvedSession?.players.filter((player) => player.status === 'waitlisted') || []
  const capacityLabel = resolvedSession
    ? `${joinedCount(resolvedSession)}/${resolvedSession.max_players} players`
    : typeof item?.spots_taken === 'number' && typeof item?.max_players === 'number'
      ? `${item.spots_taken}/${item.max_players} players`
      : null

  let primaryAction: { label: string; onClick: () => Promise<void> | void } | null = null
  let secondaryAction: { label: string; onClick: () => Promise<void> | void } | null = null

  const sessionIsFull = resolvedSession
    ? joinedCount(resolvedSession) >= resolvedSession.max_players
    : typeof item?.spots_remaining === 'number'
      ? item.spots_remaining <= 0
      : false
  const stateCopy = scheduleStateCopy({
    currentUser,
    isCreator,
    isJoined,
    isInvited,
    isWaitlisted,
    isActiveSession,
    sessionIsFull,
  })
  const rosterSummaryBits = [
    `${joinedCount(resolvedSession)} confirmed`,
    invitedPlayers.length ? `${invitedPlayers.length} invited` : null,
    waitlistedPlayers.length ? `${waitlistedPlayers.length} waiting` : null,
  ].filter(Boolean).join(' · ')
  const hostLabel = item?.creator_name || resolvedSession?.creator?.name || resolvedSession?.creator?.username || 'Third Shot'
  const gameTypeLabel = formatGameType(resolvedSession?.game_type || item?.game_type)
  const visibilityLabel = formatVisibility(resolvedSession?.visibility || item?.visibility)
  const scheduleSummaryLine = [item?.court_name || 'Court', gameTypeLabel, `Host ${hostLabel}`].filter(Boolean).join(' · ')
  const recurrenceLabel = resolvedSession?.series
    ? `Repeats ${resolvedSession.series.recurrence} · ${resolvedSession.series.sequence} of ${resolvedSession.series.occurrences}`
    : ''
  const participantIds = new Set<number>([
    ...(resolvedSession ? [resolvedSession.creator_id] : []),
    ...((resolvedSession?.players || []).map((player) => player.user_id)),
  ])
  const normalizedInviteSearch = inviteSearch.trim().toLowerCase()
  const availableFriends = friends.filter((friend) => !participantIds.has(friend.id))
  const visibleInviteFriends = availableFriends.filter((friend) => {
    if (!normalizedInviteSearch) return true
    const label = `${friend.name || ''} ${friend.username || ''}`.toLowerCase()
    return label.includes(normalizedInviteSearch)
  })
  const selectedInviteFriends = availableFriends.filter((friend) => inviteFriendIds.includes(friend.id))
  const canInviteFriends = Boolean(isCreator && isActiveSession && availableFriends.length && onInviteFriends)

  useEffect(() => {
    if (!open) return
    setInviteOpen(false)
    setInviteSearch('')
    setInvitePending(false)
    setInviteError('')
    setInviteFriendIds([])
  }, [item?.id, open, resolvedSession?.id])

  function toggleInviteFriend(friendId: number) {
    setInviteError('')
    setInviteFriendIds((current) => (
      current.includes(friendId)
        ? current.filter((id) => id !== friendId)
        : [...current, friendId]
    ))
  }

  async function sendInvites() {
    if (!resolvedSession || !onInviteFriends || !inviteFriendIds.length) return
    setInvitePending(true)
    setInviteError('')
    try {
      await onInviteFriends(resolvedSession.id, inviteFriendIds)
      setInviteFriendIds([])
      setInviteSearch('')
      setInviteOpen(false)
    } catch (error) {
      setInviteError(error instanceof Error ? error.message : 'Could not send invites.')
    } finally {
      setInvitePending(false)
    }
  }

  if (isSession && resolvedSession) {
    if (!isActiveSession) {
      primaryAction = {
        label: 'Open Court',
        onClick: () => onOpenCourt(resolvedSession.court_id),
      }
    } else if (!currentUser) {
      primaryAction = {
        label: 'Sign In To Join',
        onClick: onRequireAuth,
      }
      secondaryAction = {
        label: 'Open Court',
        onClick: () => onOpenCourt(resolvedSession.court_id),
      }
    } else if (isCreator) {
      primaryAction = {
        label: 'Open Court Hub',
        onClick: () => onOpenCourt(resolvedSession.court_id),
      }
      secondaryAction = {
        label: 'Cancel Game',
        onClick: () => onCancelSession(resolvedSession.id),
      }
    } else if (isJoined || isWaitlisted) {
      primaryAction = {
        label: 'Open Court',
        onClick: () => onOpenCourt(resolvedSession.court_id),
      }
      secondaryAction = {
        label: isWaitlisted ? 'Leave Waitlist' : 'Leave Game',
        onClick: () => onLeaveSession(resolvedSession.id),
      }
    } else if (isInvited) {
      primaryAction = {
        label: sessionIsFull ? 'Accept Invite To Waitlist' : 'Accept Invite',
        onClick: () => onJoinSession(resolvedSession.id),
      }
      secondaryAction = {
        label: 'Open Court',
        onClick: () => onOpenCourt(resolvedSession.court_id),
      }
    } else {
      primaryAction = {
        label: sessionIsFull ? 'Join Waitlist' : 'Join Game',
        onClick: () => onJoinSession(resolvedSession.id),
      }
      secondaryAction = {
        label: 'Open Court',
        onClick: () => onOpenCourt(resolvedSession.court_id),
      }
    }
  } else if (item) {
    if (!currentUser) {
      primaryAction = {
        label: 'Open Court',
        onClick: () => onOpenCourt(item.court_id),
      }
    } else {
      primaryAction = {
        label: 'Open Court',
        onClick: () => onOpenCourt(item.court_id),
      }
    }
  }

  return (
    <BottomSheet
      open={open}
      title="Game"
      onClose={onClose}
      variant="schedule"
      footer={primaryAction ? (
        <div className="schedule-sheet-footer">
          <button type="button" className="primary-btn full-width" onClick={primaryAction.onClick}>
            {primaryAction.label}
          </button>
          {secondaryAction ? (
            <button type="button" className="ghost-btn schedule-inline-action" onClick={secondaryAction.onClick}>
              {secondaryAction.label}
            </button>
          ) : null}
        </div>
      ) : null}
    >
      {!item ? (
        <div className="empty-card">Nothing selected right now.</div>
      ) : loading ? (
        <div className="empty-card">Loading schedule details...</div>
      ) : (
        <div className="sheet-grid compact-stack">
          <section className={`schedule-focus-card schedule-focus-sheet ${stateCopy.tone}`}>
            <div className="schedule-sheet-summary-top">
              <div className="section-kicker">{itemLabel(item)}</div>
              <div className="schedule-focus-status">
                <span className={`queue-pill schedule-state-pill ${stateCopy.tone}`}>{stateCopy.badge}</span>
                {sessionStatus !== 'active' ? <span className="queue-pill">{itemStatusLabel(sessionStatus)}</span> : null}
              </div>
            </div>

            <div className="schedule-sheet-title-block">
              <strong>{resolvedSession?.notes || item.title}</strong>
              <p>{scheduleSummaryLine}</p>
            </div>

            <div className={`schedule-state-banner schedule-focus-note ${stateCopy.tone}`}>
              <strong>{stateCopy.headline}</strong>
              <span>{stateCopy.detail}</span>
            </div>

            <div className="schedule-info-grid inline">
              <div>
                <span>When</span>
                <strong>{formatDateTime(resolvedSession?.start_time || item.start_time)}</strong>
              </div>
              <div>
                <span>Spots</span>
                <strong>{capacityLabel || 'Flexible'}</strong>
              </div>
              <div>
                <span>Access</span>
                <strong>{visibilityLabel}</strong>
              </div>
            </div>
            <div className="schedule-inline-meta">
              <span className="queue-pill">{gameTypeLabel}</span>
              <span className="queue-pill">{formatVisibilityPill(resolvedSession?.visibility || item.visibility)}</span>
            </div>
          </section>

          {recurrenceLabel ? (
            <div className="inline-note compact schedule-meta-note">
              {recurrenceLabel}
            </div>
          ) : null}

          {isSession && resolvedSession ? (
            <section className="schedule-roster-card compact-roster flat">
              <div className="schedule-roster-head">
                <strong>Players</strong>
                <span>{rosterSummaryBits}</span>
              </div>
              <div className="presence-strip tight-scroll">
                <span className="player-chip static compact friend">
                  {resolvedSession.creator?.name || resolvedSession.creator?.username || 'Host'}
                </span>
                {joinedPlayers.map((player) => (
                  <span key={player.id} className="player-chip static compact">
                    {player.user?.name || player.user?.username || 'Player'}
                  </span>
                ))}
              </div>
            </section>
          ) : (
            <div className="inline-note compact">
              Open the court to manage this scheduled item.
            </div>
          )}

          {canInviteFriends ? (
            <section className={`schedule-section-card subtle-card ${inviteOpen ? 'expanded' : ''}`}>
              <button
                type="button"
                className="schedule-secondary-toggle"
                onClick={() => setInviteOpen((value) => !value)}
              >
                <span>Invite friends</span>
                <strong>{inviteOpen ? 'Hide' : `${availableFriends.length} available`}</strong>
              </button>

              {inviteOpen ? (
                <div className="schedule-advanced-stack">
                  <div className="schedule-section-head">
                    <div>
                      <strong>Fill spots fast</strong>
                      <span>Select friends and send in one tap.</span>
                    </div>
                    {selectedInviteFriends.length ? (
                      <span className="queue-pill success-pill">{selectedInviteFriends.length} selected</span>
                    ) : null}
                  </div>

                  {selectedInviteFriends.length ? (
                    <div className="schedule-selected-friends">
                      {selectedInviteFriends.map((friend) => (
                        <button
                          key={friend.id}
                          type="button"
                          className="selected-friend-chip"
                          onClick={() => toggleInviteFriend(friend.id)}
                        >
                          <span>{friend.name || friend.username}</span>
                          <strong>Remove</strong>
                        </button>
                      ))}
                    </div>
                  ) : null}

                  <label className="form-field">
                    <span>Search friends</span>
                    <input
                      type="search"
                      value={inviteSearch}
                      onChange={(event) => setInviteSearch(event.target.value)}
                      placeholder="Search by name"
                    />
                  </label>

                  <div className="schedule-friend-list">
                    {visibleInviteFriends.length ? (
                      visibleInviteFriends.map((friend) => {
                        const selected = inviteFriendIds.includes(friend.id)
                        return (
                          <button
                            key={friend.id}
                            type="button"
                            className={`friend-select-row ${selected ? 'selected' : ''}`}
                            onClick={() => toggleInviteFriend(friend.id)}
                          >
                            <div>
                              <strong>{friend.name || friend.username}</strong>
                              <span>@{friend.username}</span>
                            </div>
                            <em>{selected ? 'Selected' : 'Invite'}</em>
                          </button>
                        )
                      })
                    ) : (
                      <div className="inline-note compact">No friends match that search.</div>
                    )}
                  </div>

                  {inviteError ? <div className="inline-note compact danger">{inviteError}</div> : null}

                  <button
                    type="button"
                    className="primary-btn full-width"
                    onClick={sendInvites}
                    disabled={!inviteFriendIds.length || invitePending}
                  >
                    {invitePending
                      ? 'Sending invites...'
                      : inviteFriendIds.length === 1
                        ? 'Invite 1 friend'
                        : `Invite ${inviteFriendIds.length} friends`}
                  </button>
                </div>
              ) : null}
            </section>
          ) : null}
        </div>
      )}
    </BottomSheet>
  )
}
