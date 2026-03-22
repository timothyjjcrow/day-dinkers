import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ScheduleItemSheet } from './ScheduleItemSheet'
import type { BannerItem, PlaySessionData, UserSummary } from '../types'

const currentUser: UserSummary = {
  id: 4,
  username: 'guest_player',
  name: 'Guest Player',
}

const baseItem: BannerItem = {
  id: 'session-11',
  reference_id: 11,
  item_type: 'session',
  title: 'Sunrise Doubles',
  subtitle: 'Adorni Center',
  court_id: 7,
  court_name: 'Adorni Center',
  start_time: '2026-03-21T10:00:00',
  status: 'active',
  viewer_status: 'invited',
  max_players: 4,
  spots_taken: 2,
  spots_remaining: 2,
  creator_name: 'Host Player',
}

const baseSession: PlaySessionData = {
  id: 11,
  creator_id: 2,
  court_id: 7,
  session_type: 'scheduled',
  start_time: '2026-03-21T10:00:00',
  end_time: '2026-03-21T11:30:00',
  game_type: 'doubles',
  max_players: 4,
  visibility: 'all',
  notes: 'Sunrise Doubles',
  status: 'active',
  creator: {
    id: 2,
    username: 'host_player',
    name: 'Host Player',
  },
  players: [
    {
      id: 91,
      session_id: 11,
      user_id: 4,
      status: 'invited',
      user: currentUser,
    },
    {
      id: 92,
      session_id: 11,
      user_id: 5,
      status: 'joined',
      user: {
        id: 5,
        username: 'joined_player',
        name: 'Joined Player',
      },
    },
  ],
}

describe('ScheduleItemSheet', () => {
  afterEach(() => {
    cleanup()
  })

  it('lets invited players accept a scheduled game in one tap', async () => {
    const user = userEvent.setup()
    const onJoinSession = vi.fn()

    render(
      <ScheduleItemSheet
        open
        item={baseItem}
        session={baseSession}
        currentUser={currentUser}
        onClose={() => {}}
        onOpenCourt={() => {}}
        onRequireAuth={() => {}}
        onJoinSession={onJoinSession}
        onLeaveSession={() => {}}
        onCancelSession={() => {}}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Accept Invite/i }))

    expect(onJoinSession).toHaveBeenCalledWith(11)
  })

  it('falls back to opening the court when a scheduled game has been cancelled', async () => {
    const user = userEvent.setup()
    const onOpenCourt = vi.fn()

    render(
      <ScheduleItemSheet
        open
        item={{ ...baseItem, status: 'cancelled' }}
        session={{ ...baseSession, status: 'cancelled' }}
        currentUser={currentUser}
        onClose={() => {}}
        onOpenCourt={onOpenCourt}
        onRequireAuth={() => {}}
        onJoinSession={() => {}}
        onLeaveSession={() => {}}
        onCancelSession={() => {}}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: /^Game$/i })
    expect(within(dialog).getByText(/Cancelled/i)).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: /^Open Court$/i }))

    expect(onOpenCourt).toHaveBeenCalledWith(7)
  })

  it('keeps joined players focused on the court while still allowing a fast leave action', async () => {
    const user = userEvent.setup()
    const onOpenCourt = vi.fn()
    const onLeaveSession = vi.fn()

    render(
      <ScheduleItemSheet
        open
        item={{ ...baseItem, viewer_status: 'joined' }}
        session={{
          ...baseSession,
          players: [
            {
              id: 91,
              session_id: 11,
              user_id: 4,
              status: 'joined',
              user: currentUser,
            },
          ],
        }}
        currentUser={currentUser}
        onClose={() => {}}
        onOpenCourt={onOpenCourt}
        onRequireAuth={() => {}}
        onJoinSession={() => {}}
        onLeaveSession={onLeaveSession}
        onCancelSession={() => {}}
      />,
    )

    await user.click(screen.getByRole('button', { name: /^Open Court$/i }))
    await user.click(screen.getByRole('button', { name: /^Leave Game$/i }))

    expect(onOpenCourt).toHaveBeenCalledWith(7)
    expect(onLeaveSession).toHaveBeenCalledWith(11)
  })

  it('lets hosts invite friends to an existing scheduled game', async () => {
    const user = userEvent.setup()
    const onInviteFriends = vi.fn().mockResolvedValue(undefined)

    render(
      <ScheduleItemSheet
        open
        item={{ ...baseItem, viewer_status: 'creator', is_mine: true }}
        session={{
          ...baseSession,
          creator_id: 4,
          creator: currentUser,
          players: [],
        }}
        currentUser={currentUser}
        friends={[
          {
            id: 9,
            username: 'friend_two',
            name: 'Friend Two',
          },
        ]}
        onClose={() => {}}
        onOpenCourt={() => {}}
        onRequireAuth={() => {}}
        onJoinSession={() => {}}
        onLeaveSession={() => {}}
        onCancelSession={() => {}}
        onInviteFriends={onInviteFriends}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: /^Game$/i })
    await user.click(within(dialog).getByRole('button', { name: /Invite friends/i }))
    await user.click(within(dialog).getByRole('button', { name: /Friend Two.*Invite/i }))
    await user.click(within(dialog).getByRole('button', { name: /^Invite 1 friend$/i }))

    expect(onInviteFriends).toHaveBeenCalledWith(11, [9])
  })
})
