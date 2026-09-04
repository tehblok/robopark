import {
  useEffect,
  useId,
  useRef,
  type ReactNode,
  type RefObject,
} from 'react'
import { createPortal } from 'react-dom'
import { IconButton } from '../actions/Button'
import './overlays.css'

export type DialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  initialFocusRef?: RefObject<HTMLElement | null>
  closeLabel?: string
  dismissible?: boolean
  role?: 'dialog' | 'alertdialog'
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function getFocusableElements(panel: HTMLElement) {
  return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
}

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  initialFocusRef,
  closeLabel = 'Закрыть',
  dismissible = true,
  role = 'dialog',
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  const onOpenChangeRef = useRef(onOpenChange)
  const titleId = useId()
  const descriptionId = useId()

  useEffect(() => {
    onOpenChangeRef.current = onOpenChange
  }, [onOpenChange])

  useEffect(() => {
    if (!open) return

    const priorFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    const appRoot = document.querySelector<HTMLElement>('#root')
    const rootWasInert = appRoot?.hasAttribute('inert') ?? false
    const priorOverflow = document.body.style.overflow

    appRoot?.setAttribute('inert', '')
    document.body.style.overflow = 'hidden'

    const panel = panelRef.current
    const requestedFocus = initialFocusRef?.current
    const firstFocusable = panel ? getFocusableElements(panel)[0] : undefined
    ;(requestedFocus ?? firstFocusable ?? panel)?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      const currentPanel = panelRef.current
      if (!currentPanel) return

      if (event.key === 'Escape') {
        if (dismissible) {
          event.preventDefault()
          onOpenChangeRef.current(false)
        }
        return
      }

      if (event.key !== 'Tab') return

      const focusable = getFocusableElements(currentPanel)
      if (focusable.length === 0) {
        event.preventDefault()
        currentPanel.focus()
        return
      }

      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !currentPanel.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !currentPanel.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (appRoot && !rootWasInert) appRoot.removeAttribute('inert')
      document.body.style.overflow = priorOverflow
      if (priorFocus?.isConnected) priorFocus.focus()
    }
  }, [dismissible, initialFocusRef, open])

  if (!open) return null

  const dialog = (
    <div
      className="rp-dialog-backdrop"
      onClick={(event) => {
        if (dismissible && event.currentTarget === event.target) onOpenChange(false)
      }}
    >
      <div
        aria-describedby={description ? descriptionId : undefined}
        aria-labelledby={titleId}
        aria-modal="true"
        className="rp-dialog"
        ref={panelRef}
        role={role}
        tabIndex={-1}
      >
        <header className="rp-dialog__header">
          <h2 className="rp-dialog__title" id={titleId}>{title}</h2>
          {description ? (
            <p className="rp-dialog__description" id={descriptionId}>{description}</p>
          ) : null}
        </header>
        <div className="rp-dialog__body">{children}</div>
        {footer ? <footer className="rp-dialog__footer">{footer}</footer> : null}
        {dismissible ? (
          <IconButton
            className="rp-dialog__close"
            icon="close"
            label={closeLabel}
            onClick={() => onOpenChange(false)}
            variant="ghost"
          />
        ) : null}
      </div>
    </div>
  )

  return createPortal(dialog, document.body)
}
