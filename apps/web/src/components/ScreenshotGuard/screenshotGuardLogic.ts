/** How long the deterrent overlay stays visible before auto-dismiss. */
export const SCREENSHOT_GUARD_DISMISS_MS = 8000

/** How many watermark tiles cover the viewport at rest. */
export const SCREENSHOT_GUARD_WATERMARK_COUNT = 48

/** Keyboard shortcuts commonly used for screenshots / snipping tools. */
export function isScreenshotShortcut(event: KeyboardEvent): boolean {
  if (event.key === 'PrintScreen' || event.code === 'PrintScreen') return true

  const meta = event.metaKey || event.ctrlKey
  if (event.metaKey && event.shiftKey) {
    const key = event.key.toLowerCase()
    if (key === 's') return true
    if (['3', '4', '5', '6'].includes(event.key)) return true
  }

  // Windows Game Bar / Xbox Game Bar capture.
  if (event.key === 'g' && meta) return true

  return false
}

export function buildWatermarkLabel(username: string, userId: number): string {
  return `${username} · #${userId} · Управление парком · конфиденциально`
}

export function shouldBlockProtectedAction(event: Event): boolean {
  const target = event.target
  if (!(target instanceof HTMLElement)) return true
  if (target.closest('input, textarea, select, [contenteditable="true"]')) return false
  return true
}

/** Phones/tablets: no PrintScreen, rely on watermark + lifecycle hooks. */
export function isCoarsePointerDevice(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia('(pointer: coarse)').matches
}

export function isProtectedMediaTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return Boolean(target.closest('img, svg, canvas, picture, video'))
}
