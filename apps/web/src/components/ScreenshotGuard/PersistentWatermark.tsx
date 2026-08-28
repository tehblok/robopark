import { createPortal } from 'react-dom'
import {
  buildWatermarkLabel,
  SCREENSHOT_GUARD_WATERMARK_COUNT,
} from './screenshotGuardLogic'

type PersistentWatermarkProps = {
  username: string
  userId: number
}

export function PersistentWatermark({ username, userId }: PersistentWatermarkProps) {
  const label = buildWatermarkLabel(username, userId)

  return createPortal(
    <div className="screenshot-guard-persistent" aria-hidden="true">
      {Array.from({ length: SCREENSHOT_GUARD_WATERMARK_COUNT }, (_, index) => (
        <span key={index}>{label}</span>
      ))}
    </div>,
    document.body,
  )
}
