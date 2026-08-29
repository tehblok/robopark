import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ru } from '../../i18n/ru'
import {
  buildWatermarkLabel,
  isScreenshotShortcut,
  isCoarsePointerDevice,
  isProtectedMediaTarget,
  SCREENSHOT_GUARD_DISMISS_MS,
  SCREENSHOT_GUARD_WATERMARK_COUNT,
  shouldBlockProtectedAction,
} from './screenshotGuardLogic'

type ScreenshotGuardProps = {
  username: string
  userId: number
}

export function ScreenshotGuard({ username, userId }: ScreenshotGuardProps) {
  const [visible, setVisible] = useState(false)
  const dismissTimer = useRef<number | null>(null)
  const watermarkLabel = buildWatermarkLabel(username, userId)

  const clearDismissTimer = useCallback(() => {
    if (dismissTimer.current != null) {
      window.clearTimeout(dismissTimer.current)
      dismissTimer.current = null
    }
  }, [])

  const hide = useCallback(() => {
    clearDismissTimer()
    setVisible(false)
  }, [clearDismissTimer])

  const show = useCallback(() => {
    clearDismissTimer()
    setVisible(true)
    dismissTimer.current = window.setTimeout(() => {
      setVisible(false)
      dismissTimer.current = null
    }, SCREENSHOT_GUARD_DISMISS_MS)
  }, [clearDismissTimer])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isScreenshotShortcut(event)) return
      event.preventDefault()
      event.stopPropagation()
      show()
    }

    const onContextMenu = (event: MouseEvent) => {
      if (!shouldBlockProtectedAction(event)) return
      event.preventDefault()
      show()
    }

    const onCopy = (event: ClipboardEvent) => {
      if (!shouldBlockProtectedAction(event)) return
      event.preventDefault()
      show()
    }

    const onCut = (event: ClipboardEvent) => {
      if (!shouldBlockProtectedAction(event)) return
      event.preventDefault()
      show()
    }

    const onDragStart = (event: DragEvent) => {
      if (!shouldBlockProtectedAction(event)) return
      event.preventDefault()
    }

    const onBeforePrint = () => {
      show()
    }

    const onSelectStart = (event: Event) => {
      if (!shouldBlockProtectedAction(event)) return
      event.preventDefault()
    }

    const onTouchStart = (event: TouchEvent) => {
      if (!isCoarsePointerDevice()) return
      if (!shouldBlockProtectedAction(event)) return
      if (!isProtectedMediaTarget(event.target)) return
      // Blocks iOS/Android "Save image" on long-press for media elements.
      event.preventDefault()
    }

    window.addEventListener('keydown', onKeyDown, true)
    document.addEventListener('contextmenu', onContextMenu, true)
    document.addEventListener('copy', onCopy, true)
    document.addEventListener('cut', onCut, true)
    document.addEventListener('dragstart', onDragStart, true)
    document.addEventListener('selectstart', onSelectStart, true)
    document.addEventListener('touchstart', onTouchStart, { capture: true, passive: false })
    window.addEventListener('beforeprint', onBeforePrint)

    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      document.removeEventListener('contextmenu', onContextMenu, true)
      document.removeEventListener('copy', onCopy, true)
      document.removeEventListener('cut', onCut, true)
      document.removeEventListener('dragstart', onDragStart, true)
      document.removeEventListener('selectstart', onSelectStart, true)
      document.removeEventListener('touchstart', onTouchStart, true)
      window.removeEventListener('beforeprint', onBeforePrint)
      clearDismissTimer()
    }
  }, [show, clearDismissTimer])

  if (!visible) return null

  return createPortal(
    <div
      className="screenshot-guard-overlay"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="screenshot-guard-title"
      aria-describedby="screenshot-guard-desc"
      onClick={hide}
    >
      <div className="screenshot-guard-watermark" aria-hidden="true">
        {Array.from({ length: SCREENSHOT_GUARD_WATERMARK_COUNT }, (_, index) => (
          <span key={index}>{watermarkLabel}</span>
        ))}
      </div>
      <div className="screenshot-guard-card" onClick={(event) => event.stopPropagation()}>
        <p className="screenshot-guard-brand">{ru.nav.brand}</p>
        <h2 id="screenshot-guard-title">{ru.screenshotGuard.title}</h2>
        <p id="screenshot-guard-desc">{ru.screenshotGuard.body}</p>
        <p className="screenshot-guard-user">
          {username} · {ru.screenshotGuard.confidential}
        </p>
        <button type="button" className="btn btn-secondary" onClick={hide}>
          {ru.screenshotGuard.dismiss}
        </button>
      </div>
    </div>,
    document.body,
  )
}
