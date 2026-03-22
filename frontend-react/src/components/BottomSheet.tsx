import { useEffect } from 'react'
import type { PropsWithChildren, ReactNode } from 'react'

interface BottomSheetProps extends PropsWithChildren {
  open: boolean
  title: string
  eyebrow?: string
  subtitle?: ReactNode
  onClose: () => void
  footer?: ReactNode
  variant?: 'default' | 'schedule' | 'action'
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="m7 7 10 10M17 7 7 17"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  )
}

export function BottomSheet({
  open,
  title,
  eyebrow,
  subtitle,
  onClose,
  children,
  footer,
  variant = 'default',
}: BottomSheetProps) {
  useEffect(() => {
    if (!open) return undefined

    const previousOverflow = document.body.style.overflow
    const previousHtmlOverflow = document.documentElement.style.overflow
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }

    document.body.style.overflow = 'hidden'
    document.documentElement.style.overflow = 'hidden'
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      document.body.style.overflow = previousOverflow
      document.documentElement.style.overflow = previousHtmlOverflow
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [onClose, open])

  if (!open) return null

  return (
    <div className="sheet-backdrop" onClick={onClose} role="presentation">
      <div
        className={`sheet-panel ${variant === 'schedule' ? 'schedule-sheet' : ''} ${variant === 'action' ? 'action-sheet' : ''}`.trim()}
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="sheet-handle" />
        <div className="sheet-header">
          <div className="sheet-title-block">
            {eyebrow ? <div className="section-kicker sheet-kicker">{eyebrow}</div> : null}
            <h3>{title}</h3>
            {subtitle ? <div className="sheet-subtitle">{subtitle}</div> : null}
          </div>
          <button
            type="button"
            className={variant === 'schedule' || variant === 'action' ? 'sheet-close-icon' : 'ghost-btn'}
            onClick={onClose}
            aria-label="Close"
          >
            {variant === 'schedule' || variant === 'action' ? <CloseIcon /> : 'Close'}
          </button>
        </div>
        <div className="sheet-content">{children}</div>
        {footer ? <div className="sheet-footer">{footer}</div> : null}
      </div>
    </div>
  )
}
