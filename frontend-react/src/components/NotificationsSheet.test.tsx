import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { NotificationsSheet } from './NotificationsSheet'
import type { NotificationItem, UserSummary } from '../types'

describe('NotificationsSheet', () => {
  it('routes notification taps through the selection handler', async () => {
    const user = userEvent.setup()
    const onSelectNotification = vi.fn()
    const currentUser: UserSummary = {
      id: 1,
      username: 'playerone',
      name: 'Player One',
    }
    const notifications: NotificationItem[] = [
      {
        id: 42,
        notif_type: 'checkin',
        content: 'A friend checked in nearby.',
        target_path: '/courts/7',
        target_label: 'Open court',
        read: false,
        created_at: '2026-03-20T10:00:00',
      },
    ]

    render(
      <NotificationsSheet
        open
        currentUser={currentUser}
        loading={false}
        notifications={notifications}
        onClose={() => {}}
        onRequireAuth={() => {}}
        onMarkAllRead={() => {}}
        onSelectNotification={onSelectNotification}
      />,
    )

    await user.click(screen.getByRole('button', { name: /checkin/i }))

    expect(onSelectNotification).toHaveBeenCalledWith(notifications[0])
  })
})
