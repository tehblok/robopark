const DAY_MS = 24 * 60 * 60 * 1000
const HOUR_MS = 60 * 60 * 1000

/** Presentation only: callers keep their unrounded number for logic. */
export function formatDurationHours(hours: number): string {
  return `${hours.toFixed(1)} ч`
}

/** Moscow is UTC+3 with no seasonal clock changes; work window is 06:00–18:00 UTC. */
export function moscowWorkingHoursBetween(startMs: number, endMs: number): number {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return 0
  const firstDay = Math.floor(startMs / DAY_MS)
  const lastDay = Math.floor(endMs / DAY_MS)
  const overlap = (day: number) => {
    const dayStart = day * DAY_MS
    const left = Math.max(startMs, dayStart + 6 * HOUR_MS)
    const right = Math.min(endMs, dayStart + 18 * HOUR_MS)
    return Math.max(0, right - left)
  }
  const elapsedMs = firstDay === lastDay
    ? overlap(firstDay)
    : overlap(firstDay) + overlap(lastDay) + Math.max(0, lastDay - firstDay - 1) * 12 * HOUR_MS
  return elapsedMs / HOUR_MS
}
