import type { BannerItem, ScheduleBannerData } from '../types'

function startOfDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate())
}

function addDays(date: Date, days: number) {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

export function formatDayKey(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function buildCalendarDays(data: ScheduleBannerData | null, spanDays = 14) {
  const items = [...(data?.items || [])].sort((left, right) => {
    if (!left.start_time || !right.start_time) return 0
    return left.start_time.localeCompare(right.start_time)
  })
  const summaries = new Map((data?.days || []).map((day) => [day.day_key, day]))
  const today = startOfDay(new Date())
  const firstScheduledKey = data?.days?.[0]?.day_key
  const firstScheduledDay = firstScheduledKey
    ? startOfDay(new Date(`${firstScheduledKey}T00:00:00`))
    : null
  const anchor = firstScheduledDay && firstScheduledDay > today ? firstScheduledDay : today

  return Array.from({ length: spanDays }, (_, index) => {
    const date = addDays(anchor, index)
    const dayKey = formatDayKey(date)
    const dayItems = items.filter((item) => item.start_time?.startsWith(dayKey))
    const summary = summaries.get(dayKey)

    return {
      day_key: dayKey,
      label: summary?.label
        || date.toLocaleDateString([], {
          weekday: 'short',
        }),
      date_label: summary?.date_label
        || date.toLocaleDateString([], {
          month: 'short',
          day: 'numeric',
        }),
      count: dayItems.length,
      firstItem: dayItems[0] || null,
    }
  })
}

export function itemsForDay(dayKey: string, items: BannerItem[]) {
  return [...items]
    .filter((item) => item.start_time?.startsWith(dayKey))
    .sort((left, right) => {
      if (!left.start_time || !right.start_time) return 0
      return left.start_time.localeCompare(right.start_time)
    })
}

export function resolveDaySelection(dayKey: string | null, items: BannerItem[]) {
  if (!dayKey) {
    return { type: 'clear' as const, items: [] as BannerItem[], item: null as BannerItem | null }
  }

  const dayItems = itemsForDay(dayKey, items)

  if (dayItems.length === 1) {
    return { type: 'item' as const, items: dayItems, item: dayItems[0] }
  }

  return {
    type: dayItems.length ? ('day' as const) : ('empty' as const),
    items: dayItems,
    item: null as BannerItem | null,
  }
}
