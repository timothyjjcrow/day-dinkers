import { describe, expect, it } from 'vitest'

import { resolveDaySelection } from './schedule'
import type { BannerItem } from '../types'

const baseItem: BannerItem = {
  id: 'session-1',
  reference_id: 1,
  item_type: 'session',
  title: 'Morning Open',
  subtitle: 'Court',
  court_id: 9,
  court_name: 'Carlson Park',
  start_time: '2026-03-20T09:00:00',
}

describe('resolveDaySelection', () => {
  it('opens a single scheduled game directly', () => {
    const result = resolveDaySelection('2026-03-20', [baseItem])

    expect(result.type).toBe('item')
    expect(result.item?.reference_id).toBe(1)
  })

  it('keeps multi-game days in the day sheet flow', () => {
    const result = resolveDaySelection('2026-03-20', [
      baseItem,
      {
        ...baseItem,
        id: 'session-2',
        reference_id: 2,
        title: 'Evening Ladder',
        start_time: '2026-03-20T18:00:00',
      },
    ])

    expect(result.type).toBe('day')
    expect(result.items).toHaveLength(2)
  })
})
