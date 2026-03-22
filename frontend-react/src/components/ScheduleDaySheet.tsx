import { BottomSheet } from './BottomSheet'
import type { BannerItem } from '../types'

function formatTime(value: string | null | undefined) {
  if (!value) return 'Time TBD'
  return new Date(value).toLocaleString([], {
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatGameType(value: string | null | undefined) {
  const normalized = String(value || 'open').replace(/_/g, ' ').trim()
  if (!normalized) return 'Open play'
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function itemBadge(item: BannerItem) {
  if (item.item_type === 'ranked_lobby') return 'Ranked'
  if (item.item_type === 'tournament') return 'Tournament'
  return 'Session'
}

function statusLabel(item: BannerItem) {
  if (item.item_type !== 'session') return itemBadge(item)
  if (item.viewer_status === 'creator') return 'Hosting'
  if (item.viewer_status === 'joined') return 'Joined'
  if (item.viewer_status === 'invited') return 'Invited'
  if (item.viewer_status === 'waitlisted') return 'Waitlisted'
  if (typeof item.spots_remaining === 'number') {
    return item.spots_remaining > 0
      ? `${item.spots_remaining} open`
      : 'Full'
  }
  return 'Open'
}

function actionLabel(item: BannerItem) {
  if (item.item_type !== 'session') return 'Details'
  if (item.viewer_status === 'creator') return 'Manage'
  if (item.viewer_status === 'joined') return 'Open'
  if (item.viewer_status === 'invited') {
    return typeof item.spots_remaining === 'number' && item.spots_remaining <= 0 ? 'Waitlist' : 'Accept'
  }
  if (item.viewer_status === 'waitlisted') return 'Open'
  if (typeof item.spots_remaining === 'number') {
    return item.spots_remaining > 0 ? 'Join' : 'Waitlist'
  }
  return 'Open'
}

function secondaryBadge(item: BannerItem) {
  if (item.is_mine) return 'Mine'
  if (item.is_friend_only) return 'Friends'
  return ''
}

interface ScheduleDaySheetProps {
  open: boolean
  title: string
  items: BannerItem[]
  onClose: () => void
  onCreate: () => void
  onSelectItem: (item: BannerItem) => void
}

export function ScheduleDaySheet({
  open,
  title,
  items,
  onClose,
  onCreate,
  onSelectItem,
}: ScheduleDaySheetProps) {
  return (
    <BottomSheet
      open={open}
      title="Day schedule"
      onClose={onClose}
      variant="schedule"
      footer={
        <button type="button" className="primary-btn full-width" onClick={onCreate}>
          Schedule Game
        </button>
      }
    >
      <div className="schedule-day-sheet-hero">
        <div className="schedule-day-sheet-head compact-copy">
          <div className="section-kicker">Day</div>
          <div className="schedule-day-sheet-head-row">
            <strong>{title}</strong>
            <span className="queue-pill">
              {items.length ? `${items.length} planned` : 'Open day'}
            </span>
          </div>
          <span>
            {items.length
              ? 'Tap a game to join, manage, or jump into the court.'
              : 'Nothing is posted yet. Schedule a game for this day.'}
          </span>
        </div>
      </div>

      <div className="schedule-list compact day-sheet-list">
        {items.length ? (
          items.map((item) => (
            <button
              key={item.id}
              type="button"
              className="schedule-card interactive day-sheet-card refined"
              onClick={() => onSelectItem(item)}
            >
              <div className="schedule-day-time">
                <strong>{formatTime(item.start_time)}</strong>
                <span>{itemBadge(item)}</span>
              </div>
              <div className="schedule-day-main">
                <div className="schedule-day-main-top">
                  <strong>{item.title}</strong>
                  <span className="schedule-day-item-action">{actionLabel(item)}</span>
                </div>
                <div className="schedule-card-top schedule-day-tags slim">
                  {secondaryBadge(item) ? <span className="schedule-tag mine">{secondaryBadge(item)}</span> : null}
                  <span className="schedule-tag subtle">{statusLabel(item)}</span>
                </div>
                <div className="schedule-card-foot compact-meta day-sheet-meta">
                  <span>{item.court_name}</span>
                  <span>{formatGameType(item.game_type)}</span>
                </div>
              </div>
            </button>
          ))
        ) : (
          <div className="empty-card">Nothing scheduled for this day yet.</div>
        )}
      </div>
    </BottomSheet>
  )
}
