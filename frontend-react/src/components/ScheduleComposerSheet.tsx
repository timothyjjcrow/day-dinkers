import { useEffect, useState } from 'react'

import type { CourtSummary, UserSummary } from '../types'
import { BottomSheet } from './BottomSheet'

interface ScheduleComposerSheetProps {
  open: boolean
  currentUser: UserSummary | null
  lockedCourtId: number | null
  lockedCourtName?: string | null
  initialDayKey?: string | null
  courtOptions: CourtSummary[]
  loadingCourts?: boolean
  friends: UserSummary[]
  onClose: () => void
  onRequireAuth: () => void
  onSubmit: (values: {
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
  }) => Promise<void> | void
}

const DURATION_OPTIONS = ['60', '90', '120']
const QUICK_TIME_OPTIONS = [
  ['08:00', 'Morning'],
  ['12:00', 'Noon'],
  ['18:00', 'Evening'],
] as const
const GAME_TYPE_OPTIONS = [
  ['open', 'Open Play'],
  ['singles', 'Singles'],
  ['doubles', 'Doubles'],
] as const
const VISIBILITY_OPTIONS = [
  ['all', 'Everyone'],
  ['friends', 'Friends'],
] as const
const RECURRENCE_OPTIONS = [
  ['none', 'One time'],
  ['weekly', 'Weekly'],
  ['biweekly', 'Biweekly'],
] as const

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function formatDateValue(date: Date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function formatTimeValue(date: Date) {
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function buildInitialSchedule(dayKey: string | null | undefined) {
  const now = new Date()
  const initial = new Date(now)
  initial.setSeconds(0, 0)
  initial.setMinutes(initial.getMinutes() < 30 ? 30 : 0)
  if (now.getMinutes() >= 30) {
    initial.setHours(initial.getHours() + 1)
  }
  if (initial.getHours() >= 21) {
    initial.setDate(initial.getDate() + 1)
    initial.setHours(8, 0, 0, 0)
  }
  if (dayKey) {
    const selectedDate = new Date(`${dayKey}T18:00:00`)
    return {
      date: formatDateValue(selectedDate),
      time: '18:00',
    }
  }
  return {
    date: formatDateValue(initial),
    time: formatTimeValue(initial),
  }
}

function maxPlayerOptionsFor(gameType: 'open' | 'singles' | 'doubles') {
  if (gameType === 'singles') return ['2', '4']
  if (gameType === 'doubles') return ['4', '6', '8']
  return ['4', '8', '12']
}

function defaultMaxPlayersFor(gameType: 'open' | 'singles' | 'doubles') {
  if (gameType === 'singles') return '2'
  if (gameType === 'doubles') return '4'
  return '8'
}

function formatScheduleSummary(dateValue: string, timeValue: string) {
  if (!dateValue || !timeValue) return 'Pick a day and time'
  return new Date(`${dateValue}T${timeValue}`).toLocaleString([], {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function fallbackTitle(gameType: 'open' | 'singles' | 'doubles') {
  if (gameType === 'singles') return 'Singles Game'
  if (gameType === 'doubles') return 'Doubles Game'
  return 'Open Play'
}

function gameTypeLabel(value: 'open' | 'singles' | 'doubles') {
  return GAME_TYPE_OPTIONS.find(([option]) => option === value)?.[1] || 'Open Play'
}

function visibilityLabel(value: 'all' | 'friends') {
  return VISIBILITY_OPTIONS.find(([option]) => option === value)?.[1] || 'Everyone'
}

function scheduleActionLabel(pending: boolean, inviteCount: number) {
  if (pending) return 'Saving...'
  if (!inviteCount) return 'Save game'
  return inviteCount === 1 ? 'Save + invite 1' : `Save + invite ${inviteCount}`
}

export function ScheduleComposerSheet({
  open,
  currentUser,
  lockedCourtId,
  lockedCourtName,
  initialDayKey,
  courtOptions,
  loadingCourts = false,
  friends,
  onClose,
  onRequireAuth,
  onSubmit,
}: ScheduleComposerSheetProps) {
  const [pending, setPending] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [friendPickerOpen, setFriendPickerOpen] = useState(false)
  const [friendSearch, setFriendSearch] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [form, setForm] = useState({
    courtId: '',
    title: '',
    date: '',
    time: '',
    durationMinutes: '90',
    gameType: 'open' as 'open' | 'singles' | 'doubles',
    visibility: 'all' as 'all' | 'friends',
    maxPlayers: '8',
    recurrence: 'none' as 'none' | 'weekly' | 'biweekly',
    recurrenceCount: '1',
    inviteFriendIds: [] as number[],
  })

  useEffect(() => {
    if (!open) return
    const initialSchedule = buildInitialSchedule(initialDayKey)
    setAdvancedOpen(false)
    setFriendPickerOpen(false)
    setFriendSearch('')
    setErrorMessage('')
    setForm({
      courtId: lockedCourtId ? String(lockedCourtId) : '',
      title: '',
      date: initialSchedule.date,
      time: initialSchedule.time,
      durationMinutes: '90',
      gameType: 'open',
      visibility: 'all',
      maxPlayers: '8',
      recurrence: 'none',
      recurrenceCount: '1',
      inviteFriendIds: [],
    })
  }, [initialDayKey, lockedCourtId, open])

  useEffect(() => {
    if (!open || lockedCourtId) return
    if (!courtOptions.length) return
    if (form.courtId && courtOptions.some((court) => String(court.id) === form.courtId)) {
      return
    }
    setForm((current) => ({
      ...current,
      courtId: String(courtOptions[0].id),
    }))
  }, [courtOptions, form.courtId, lockedCourtId, open])

  function toggleFriend(friendId: number) {
    setErrorMessage('')
    setForm((current) => ({
      ...current,
      inviteFriendIds: current.inviteFriendIds.includes(friendId)
        ? current.inviteFriendIds.filter((id) => id !== friendId)
        : [...current.inviteFriendIds, friendId],
    }))
  }

  async function submit() {
    setErrorMessage('')
    if (!currentUser) {
      onClose()
      onRequireAuth()
      return
    }
    if (!form.courtId) {
      setErrorMessage('Choose a court before scheduling.')
      return
    }
    if (!form.date || !form.time) {
      setErrorMessage('Pick a day and start time.')
      return
    }
    if (!lockedCourtId && !courtOptions.length && !loadingCourts) {
      setErrorMessage('No courts are available to schedule in this area yet.')
      return
    }

    setPending(true)
    try {
      await onSubmit({
        courtId: Number(form.courtId),
        title: form.title.trim() || fallbackTitle(form.gameType),
        startTime: `${form.date}T${form.time}`,
        durationMinutes: Number(form.durationMinutes),
        gameType: form.gameType,
        visibility: form.visibility,
        maxPlayers: Number(form.maxPlayers),
        inviteFriendIds: form.inviteFriendIds,
        recurrence: form.recurrence,
        recurrenceCount: Number(form.recurrenceCount),
      })
      const initialSchedule = buildInitialSchedule(initialDayKey)
      setForm({
        courtId: lockedCourtId ? String(lockedCourtId) : '',
        title: '',
        date: initialSchedule.date,
        time: initialSchedule.time,
        durationMinutes: '90',
        gameType: 'open',
        visibility: 'all',
        maxPlayers: '8',
        recurrence: 'none',
        recurrenceCount: '1',
        inviteFriendIds: [],
      })
      setAdvancedOpen(false)
      setFriendPickerOpen(false)
      setFriendSearch('')
      setErrorMessage('')
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Could not schedule this game.')
    } finally {
      setPending(false)
    }
  }

  const selectedCourtName = lockedCourtName
    || courtOptions.find((court) => String(court.id) === form.courtId)?.name
    || (loadingCourts ? 'Loading courts...' : 'Choose a court')
  const playerOptions = maxPlayerOptionsFor(form.gameType)
  const canSubmit = Boolean(form.courtId && form.date && form.time && !pending)
  const selectedFriends = friends.filter((friend) => form.inviteFriendIds.includes(friend.id))
  const normalizedFriendSearch = friendSearch.trim().toLowerCase()
  const visibleFriends = friends.filter((friend) => {
    if (!normalizedFriendSearch) return true
    const label = `${friend.name || ''} ${friend.username || ''}`.toLowerCase()
    return label.includes(normalizedFriendSearch)
  })
  const scheduleSummaryPills = [
    gameTypeLabel(form.gameType),
    visibilityLabel(form.visibility),
    `${form.durationMinutes} min`,
    `${form.maxPlayers} players`,
  ]
  const helperCopy = form.visibility === 'friends'
    ? 'Friends only join.'
    : selectedFriends.length
      ? 'Invites go out now. Others can still join.'
      : 'Anyone can join once it posts.'

  return (
    <BottomSheet
      open={open}
      title="Schedule game"
      onClose={onClose}
      variant="schedule"
      footer={
        <div className="schedule-sheet-footer">
          {errorMessage ? <div className="inline-note compact danger">{errorMessage}</div> : null}
          <button type="button" className="primary-btn full-width" onClick={submit} disabled={!canSubmit}>
            {scheduleActionLabel(pending, form.inviteFriendIds.length)}
          </button>
        </div>
      }
    >
      <div className="sheet-grid compact-stack schedule-composer-stack">
        <section className="schedule-composer-summary elevated">
          <div className="schedule-section-head summary-head">
            <div>
              <div className="section-kicker">Quick setup</div>
              <strong>{selectedCourtName}</strong>
            </div>
            {selectedFriends.length ? (
              <span className="queue-pill success-pill">{selectedFriends.length} invited</span>
            ) : (
              <span className="queue-pill">No invites</span>
            )}
          </div>
          <span>{formatScheduleSummary(form.date, form.time)}</span>
          <div className="schedule-composer-summary-meta">
            {scheduleSummaryPills.map((pill) => (
              <span key={pill}>{pill}</span>
            ))}
          </div>
        </section>

        <section className="schedule-section-card">
          <div className="schedule-section-head">
            <div>
              <strong>Time</strong>
              <span>Court, day, and start time.</span>
            </div>
          </div>

          {lockedCourtId ? (
            <div className="locked-field compact-card">
              <span>Court</span>
              <strong>{lockedCourtName || `Court ${lockedCourtId}`}</strong>
            </div>
          ) : (
            <label className="form-field">
              <span>Court</span>
              <select
                value={form.courtId}
                disabled={loadingCourts || !courtOptions.length}
                onChange={(event) => setForm((current) => ({ ...current, courtId: event.target.value }))}
              >
                {!courtOptions.length ? (
                  <option value="">
                    {loadingCourts ? 'Loading courts...' : 'No courts available'}
                  </option>
                ) : null}
                {courtOptions.map((court) => (
                  <option key={court.id} value={court.id}>
                    {court.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          <div className="schedule-composer-grid">
            <label className="form-field">
              <span>Day</span>
              <input
                type="date"
                value={form.date}
                onChange={(event) => {
                  setErrorMessage('')
                  setForm((current) => ({ ...current, date: event.target.value }))
                }}
              />
            </label>
            <label className="form-field">
              <span>Start time</span>
              <input
                type="time"
                step="900"
                value={form.time}
                onChange={(event) => {
                  setErrorMessage('')
                  setForm((current) => ({ ...current, time: event.target.value }))
                }}
              />
            </label>
          </div>

          <div className="form-field">
            <span>Quick time</span>
            <div className="chip-row compact">
              {QUICK_TIME_OPTIONS.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={form.time === value ? 'chip active' : 'chip'}
                  onClick={() => {
                    setErrorMessage('')
                    setForm((current) => ({ ...current, time: value }))
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="schedule-section-card">
          <div className="schedule-section-head">
            <div>
              <strong>Play setup</strong>
              <span>{helperCopy}</span>
            </div>
          </div>

          <div className="form-field">
            <span>Game type</span>
            <div className="chip-row compact">
              {GAME_TYPE_OPTIONS.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={form.gameType === value ? 'chip active' : 'chip'}
                  onClick={() =>
                    setForm((current) => ({
                      ...current,
                      gameType: value as 'open' | 'singles' | 'doubles',
                      maxPlayers: defaultMaxPlayersFor(value as 'open' | 'singles' | 'doubles'),
                    }))
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="form-field">
            <span>Access</span>
            <div className="chip-row compact">
              {VISIBILITY_OPTIONS.map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  className={form.visibility === value ? 'chip active' : 'chip'}
                  onClick={() =>
                    setForm((current) => ({
                      ...current,
                      visibility: value as 'all' | 'friends',
                    }))
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="schedule-composer-grid">
            <div className="form-field">
              <span>Duration</span>
              <div className="chip-row compact">
                {DURATION_OPTIONS.map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={form.durationMinutes === value ? 'chip active' : 'chip'}
                    onClick={() => setForm((current) => ({ ...current, durationMinutes: value }))}
                  >
                    {value} min
                  </button>
                ))}
              </div>
            </div>

            <div className="form-field">
              <span>Max players</span>
              <div className="chip-row compact">
                {playerOptions.map((value) => (
                  <button
                    key={value}
                    type="button"
                    className={form.maxPlayers === value ? 'chip active' : 'chip'}
                    onClick={() => setForm((current) => ({ ...current, maxPlayers: value }))}
                  >
                    {value} players
                  </button>
                ))}
              </div>
            </div>
          </div>
        </section>

        {friends.length ? (
          <section className="schedule-section-card">
            <div className="schedule-section-head">
              <div>
                <strong>Invite friends</strong>
                <span>
                  {selectedFriends.length
                    ? `${selectedFriends.length} friend${selectedFriends.length > 1 ? 's' : ''} selected`
                    : 'Optional and easy to add now.'}
                </span>
              </div>
              <button
                type="button"
                className="ghost-btn inline-toggle"
                onClick={() => setFriendPickerOpen((value) => !value)}
              >
                {friendPickerOpen ? 'Done' : 'Add'}
              </button>
            </div>

            {selectedFriends.length ? (
              <div className="schedule-selected-friends">
                {selectedFriends.map((friend) => (
                  <button
                    key={friend.id}
                    type="button"
                    className="selected-friend-chip"
                    onClick={() => toggleFriend(friend.id)}
                  >
                    <span>{friend.name || friend.username}</span>
                    <strong>Remove</strong>
                  </button>
                ))}
              </div>
            ) : (
              <div className="inline-note compact">
                Invite friends now or keep the game open to join later.
              </div>
            )}

            {friendPickerOpen ? (
              <div className="schedule-friend-picker">
                <label className="form-field">
                  <span>Search friends</span>
                  <input
                    type="search"
                    value={friendSearch}
                    onChange={(event) => setFriendSearch(event.target.value)}
                    placeholder="Search by name"
                  />
                </label>

                <div className="schedule-friend-list">
                  {visibleFriends.length ? (
                    visibleFriends.map((friend) => {
                      const selected = form.inviteFriendIds.includes(friend.id)
                      return (
                        <button
                          key={friend.id}
                          type="button"
                          className={`friend-select-row ${selected ? 'selected' : ''}`}
                          onClick={() => toggleFriend(friend.id)}
                        >
                          <div>
                            <strong>{friend.name || friend.username}</strong>
                            <span>@{friend.username}</span>
                          </div>
                          <em>{selected ? 'Invited' : 'Invite'}</em>
                        </button>
                      )
                    })
                  ) : (
                    <div className="inline-note compact">No friends match that search.</div>
                  )}
                </div>
              </div>
            ) : null}
          </section>
        ) : null}

        <section className={`schedule-section-card subtle-card ${advancedOpen ? 'expanded' : ''}`}>
          <button
            type="button"
            className="schedule-secondary-toggle"
            onClick={() => setAdvancedOpen((value) => !value)}
          >
            <span>{advancedOpen ? 'Hide details' : 'More options'}</span>
            <strong>{advancedOpen ? 'Less' : 'Optional'}</strong>
          </button>

          {advancedOpen ? (
            <div className="schedule-advanced-stack">
              <label className="form-field">
                <span>Name (optional)</span>
                <input
                  type="text"
                  value={form.title}
                  onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
                  placeholder="Sunrise run, ladder tune-up, open play..."
                />
              </label>

              <div className="form-field">
                <span>Repeats</span>
                <div className="chip-row compact">
                  {RECURRENCE_OPTIONS.map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      className={form.recurrence === value ? 'chip active' : 'chip'}
                      onClick={() =>
                        setForm((current) => ({
                          ...current,
                          recurrence: value as 'none' | 'weekly' | 'biweekly',
                          recurrenceCount: value === 'none' ? '1' : current.recurrenceCount,
                        }))
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {form.recurrence !== 'none' ? (
                <label className="form-field">
                  <span>Occurrences</span>
                  <select
                    value={form.recurrenceCount}
                    onChange={(event) => setForm((current) => ({ ...current, recurrenceCount: event.target.value }))}
                  >
                    {[2, 3, 4, 6, 8, 12].map((value) => (
                      <option key={value} value={value}>
                        {value} sessions
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
          ) : null}
        </section>
      </div>
    </BottomSheet>
  )
}
