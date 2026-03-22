import type { BannerItem, ScheduleBannerData } from '../types'
import { buildCalendarDays } from '../lib/schedule'

interface ScheduleBannerViewProps {
  data: ScheduleBannerData | null
  compact?: boolean
  className?: string
  expanded: boolean
  mineOnly?: boolean
  selectedDayKey: string | null
  onToggle: () => void
  onToggleMineOnly?: () => void
  onSelectDay: (dayKey: string | null) => void
  onCreate?: () => void
  onSelectItem?: (item: BannerItem) => void
}

function formatTime(isoString: string | null | undefined) {
  if (!isoString) return 'TBD'
  return new Date(isoString).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatDayHint(isoString: string | null | undefined) {
  if (!isoString) return 'Open'
  return new Date(isoString).toLocaleString([], {
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatCompactBannerTitle(dayKey: string | null, mineOnly: boolean, hasNextItem: boolean) {
  if (dayKey) {
    return new Date(`${dayKey}T00:00:00`).toLocaleDateString([], {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
    })
  }
  if (mineOnly) return 'My plans'
  return hasNextItem ? 'Next up' : 'Open week'
}

export function ScheduleBannerView({
  data,
  compact = false,
  className = '',
  expanded,
  mineOnly = false,
  selectedDayKey,
  onToggle: _onToggle,
  onToggleMineOnly,
  onSelectDay,
  onCreate,
  onSelectItem: _onSelectItem,
}: ScheduleBannerViewProps) {
  const items = data?.items || []
  const nextItem = items[0]
  const calendarDays = buildCalendarDays(data)
  const activeDay = calendarDays.find((day) => day.day_key === selectedDayKey) || null
  const bannerTitle = compact
    ? formatCompactBannerTitle(selectedDayKey, mineOnly, Boolean(nextItem))
    : selectedDayKey ? `Games for ${selectedDayKey}` : 'Next few weeks'
  const bannerSummary = selectedDayKey
    ? activeDay?.count
      ? `${activeDay.count} scheduled · ${activeDay.firstItem?.title || 'Tap to open'}`
      : 'No scheduled games for this day.'
    : nextItem
      ? `${formatTime(nextItem.start_time)} · ${nextItem.title}`
      : 'Tap a day to browse upcoming play.'

  return (
    <section className={`schedule-banner ${compact ? 'compact' : ''} ${expanded ? 'expanded' : ''} ${className}`.trim()}>
      <div className="schedule-banner-header">
        <div className="schedule-banner-title static" aria-expanded={expanded}>
          <div className="section-kicker">Schedule</div>
          <strong>{bannerTitle}</strong>
          {compact ? null : <p>{bannerSummary}</p>}
        </div>

        <div className="schedule-banner-actions">
          {onToggleMineOnly ? (
            <button
              type="button"
              className={mineOnly ? 'chip active' : 'chip'}
              onClick={onToggleMineOnly}
            >
              {compact ? 'Mine' : 'My plans'}
            </button>
          ) : null}
          {onCreate ? (
            <button type="button" className="chip" onClick={onCreate}>
              New
            </button>
          ) : null}
        </div>
      </div>

      <div className="schedule-rail">
        {calendarDays.map((day) => {
          const active = selectedDayKey === day.day_key
          return (
            <button
              key={day.day_key}
              type="button"
              className={`schedule-day-card ${active ? 'active' : ''}`}
              onClick={() => onSelectDay(active ? null : day.day_key)}
            >
              <div className="schedule-day-card-top">
                <span>{day.label}</span>
                <strong>{day.date_label}</strong>
              </div>
              <div className="schedule-day-card-body">
                <span className="schedule-day-card-count">
                  {compact
                    ? (day.count ? `${day.count}` : 'Open')
                    : day.count ? `${day.count} game${day.count > 1 ? 's' : ''}` : 'Open'}
                </span>
                {compact ? (
                  <small className="schedule-day-card-hint">
                    {day.count ? formatDayHint(day.firstItem?.start_time) : 'Open'}
                  </small>
                ) : (
                  <p>{day.firstItem ? day.firstItem.title : 'No scheduled play yet'}</p>
                )}
              </div>
            </button>
          )
        })}
      </div>
    </section>
  )
}
