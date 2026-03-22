import { BottomSheet } from './BottomSheet'
import type { NotificationItem, UserSummary } from '../types'

interface NotificationsSheetProps {
  open: boolean
  currentUser: UserSummary | null
  loading: boolean
  notifications: NotificationItem[]
  onClose: () => void
  onRequireAuth: () => void
  onMarkAllRead: () => Promise<void> | void
  onSelectNotification: (notification: NotificationItem) => Promise<void> | void
}

function formatTime(value: string) {
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function NotificationsSheet({
  open,
  currentUser,
  loading,
  notifications,
  onClose,
  onRequireAuth,
  onMarkAllRead,
  onSelectNotification,
}: NotificationsSheetProps) {
  return (
    <BottomSheet
      open={open}
      title="Alerts"
      eyebrow="Inbox"
      subtitle={currentUser ? 'Invites, score updates, and friend activity in one place' : 'Sign in to follow invites, scores, and friend activity'}
      onClose={onClose}
      variant="action"
      footer={
        currentUser && notifications.length ? (
          <button type="button" className="secondary-btn full-width" onClick={onMarkAllRead}>
            Mark all read
          </button>
        ) : null
      }
    >
      {!currentUser ? (
        <div className="empty-card">
          Sign in to see invitations, score confirmations, and friend activity.
          <button type="button" className="primary-btn full-width" onClick={onRequireAuth}>
            Sign In
          </button>
        </div>
      ) : loading ? (
        <div className="empty-card">Loading notifications...</div>
      ) : notifications.length ? (
        <div className="notifications-list">
          {notifications.map((notification) => (
            <button
              type="button"
              key={notification.id}
              className={`notification-card ${notification.read ? 'read' : 'unread'}`}
              onClick={() => onSelectNotification(notification)}
            >
              <strong>{notification.notif_type.replace(/_/g, ' ')}</strong>
              <p>{notification.content}</p>
              <div className="notification-card-footer">
                <span>{formatTime(notification.created_at)}</span>
                {notification.target_label ? <em>{notification.target_label}</em> : null}
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="empty-card">Nothing new right now. Fresh invites and updates will land here.</div>
      )}
    </BottomSheet>
  )
}
