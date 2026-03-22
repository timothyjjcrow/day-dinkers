import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ScheduleComposerSheet } from './ScheduleComposerSheet'
import type { CourtSummary, UserSummary } from '../types'

const currentUser: UserSummary = {
  id: 1,
  username: 'scheduler',
  name: 'Scheduler',
}

const courtOptions: CourtSummary[] = [
  {
    id: 9,
    name: 'Adorni Center',
    latitude: 40.8,
    longitude: -124.16,
  },
]

const friends: UserSummary[] = [
  {
    id: 7,
    username: 'friend_one',
    name: 'Friend One',
  },
]

describe('ScheduleComposerSheet', () => {
  afterEach(() => {
    cleanup()
  })

  it('submits recurrence and invited friends with the scheduled game payload', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockResolvedValue(undefined)

    render(
      <ScheduleComposerSheet
        open
        currentUser={currentUser}
        lockedCourtId={null}
        initialDayKey={null}
        courtOptions={courtOptions}
        friends={friends}
        onClose={() => {}}
        onRequireAuth={() => {}}
        onSubmit={onSubmit}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: /Schedule game/i })

    fireEvent.change(within(dialog).getByLabelText(/Day/i), {
      target: { value: '2026-03-22' },
    })
    fireEvent.change(within(dialog).getByLabelText(/Start time/i), {
      target: { value: '08:30' },
    })
    await user.click(within(dialog).getByRole('button', { name: /^Doubles$/i }))
    await user.click(within(dialog).getByRole('button', { name: /^Friends$/i }))
    await user.click(within(dialog).getByRole('button', { name: /^Add$/i }))
    await user.click(within(dialog).getByRole('button', { name: /More options/i }))
    await user.type(within(dialog).getByLabelText(/Name \(optional\)/i), 'Morning Ladder')
    await user.click(within(dialog).getByRole('button', { name: /^Weekly$/i }))
    await user.selectOptions(within(dialog).getByLabelText(/Occurrences/i), '3')
    await user.click(within(dialog).getByRole('button', { name: /Friend One.*Invite/i }))
    await user.click(within(dialog).getByRole('button', { name: /Save \+ invite 1/i }))

    expect(onSubmit).toHaveBeenCalledWith({
      courtId: 9,
      title: 'Morning Ladder',
      startTime: '2026-03-22T08:30',
      durationMinutes: 90,
      gameType: 'doubles',
      visibility: 'friends',
      maxPlayers: 4,
      inviteFriendIds: [7],
      recurrence: 'weekly',
      recurrenceCount: 3,
    })
  }, 15000)

  it('surfaces scheduling errors inline instead of failing silently', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn().mockRejectedValue(new Error('Court is unavailable at that time'))

    render(
      <ScheduleComposerSheet
        open
        currentUser={currentUser}
        lockedCourtId={null}
        initialDayKey="2026-03-22"
        courtOptions={courtOptions}
        friends={friends}
        onClose={() => {}}
        onRequireAuth={() => {}}
        onSubmit={onSubmit}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: /Schedule game/i })
    await user.click(within(dialog).getByRole('button', { name: /^Save game$/i }))

    expect(await within(dialog).findByText(/Court is unavailable at that time/i)).toBeInTheDocument()
  })
})
