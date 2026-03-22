import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ScheduleBannerView } from './ScheduleBanner'
import type { ScheduleBannerData } from '../types'
import { formatDayKey } from '../lib/schedule'

const baseDate = new Date()
baseDate.setDate(baseDate.getDate() + 1)
baseDate.setHours(11, 30, 0, 0)

const dayKey = formatDayKey(baseDate)
const dayLabel = baseDate.toLocaleDateString([], { weekday: 'short' })
const dateLabel = baseDate.toLocaleDateString([], { month: 'short', day: 'numeric' })
const startTime = `${dayKey}T11:30:00`

const bannerData: ScheduleBannerData = {
  items: [
    {
      id: 'session-12',
      reference_id: 12,
      item_type: 'session',
      title: 'Lunch Run',
      subtitle: 'Main Court',
      court_id: 9,
      court_name: 'Main Court',
      start_time: startTime,
      is_mine: true,
    },
  ],
  days: [
    {
      day_key: dayKey,
      label: dayLabel,
      date_label: dateLabel,
      count: 1,
    },
  ],
  context: {
    court_id: null,
    county_slug: 'alameda',
    user_only: false,
  },
}

describe('ScheduleBannerView', () => {
  it('routes compact rail interactions through the scheduling callbacks', async () => {
    const user = userEvent.setup()
    const onSelectDay = vi.fn()
    const onToggleMineOnly = vi.fn()
    const onCreate = vi.fn()

    render(
      <ScheduleBannerView
        data={bannerData}
        compact
        expanded
        mineOnly={false}
        selectedDayKey={null}
        onToggle={vi.fn()}
        onToggleMineOnly={onToggleMineOnly}
        onSelectDay={onSelectDay}
        onCreate={onCreate}
      />,
    )

    await user.click(screen.getByRole('button', { name: new RegExp(`${dayLabel}\\s+${dateLabel}`, 'i') }))
    await user.click(screen.getByRole('button', { name: /Mine/i }))
    await user.click(screen.getByRole('button', { name: /New/i }))

    expect(onSelectDay).toHaveBeenCalledWith(dayKey)
    expect(onToggleMineOnly).toHaveBeenCalled()
    expect(onCreate).toHaveBeenCalled()
  })
})
