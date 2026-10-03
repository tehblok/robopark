const DAY_MS = 24 * 60 * 60 * 1000
const HOUR_MS = 60 * 60 * 1000
const formatterCache = new Map<string, Intl.DateTimeFormat>()
const parkDateFormatterCache = new Map<string, Intl.DateTimeFormat>()
const windowCache = new Map<string, readonly [number, number]>()
const MAX_CACHED_WINDOWS = 4096
const MAX_DATE_FORMATTERS = 64

/** Presentation only: callers keep their unrounded number for logic. */
export function formatDurationHours(hours: number): string {
  return `${hours.toFixed(1)} ч`
}

/** Display one confirmed timestamp in the park's local time without per-row formatter creation. */
export function formatParkDateTime(value: string | null, timezone: string): string | null {
  if (!value || !Number.isFinite(Date.parse(value))) return null
  try {
    let formatter = parkDateFormatterCache.get(timezone)
    if (!formatter) {
      formatter = new Intl.DateTimeFormat('ru-RU', {
        timeZone: timezone, day: '2-digit', month: '2-digit',
        hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
      })
      if (parkDateFormatterCache.size >= MAX_DATE_FORMATTERS) {
        parkDateFormatterCache.delete(parkDateFormatterCache.keys().next().value!)
      }
      parkDateFormatterCache.set(timezone, formatter)
    }
    return formatter.format(new Date(value))
  } catch (error) {
    if (error instanceof RangeError) return null
    throw error
  }
}

/** Intersect UTC time with the park's daily 09:00–21:00 local window. */
export function workingHoursBetween(startMs: number, endMs: number, timezone: string): number {
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return 0
  let formatter = formatterCache.get(timezone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    })
    formatterCache.set(timezone, formatter)
  }
  const localParts = (timestamp: number) => Object.fromEntries(
    formatter.formatToParts(timestamp).filter(part => part.type !== 'literal').map(part => [part.type, Number(part.value)]),
  ) as Record<'year' | 'month' | 'day' | 'hour' | 'minute' | 'second', number>
  const start = localParts(startMs)
  const finish = localParts(endMs)
  const firstDay = Date.UTC(start.year, start.month - 1, start.day)
  const lastDay = Date.UTC(finish.year, finish.month - 1, finish.day)
  const utcForLocal = (day: number, hour: number) => {
    const local = day + hour * HOUR_MS
    let guess = local
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const parts = localParts(guess)
      const observed = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second)
      const difference = local - observed
      if (difference === 0) break
      guess += difference
    }
    return guess
  }
  let elapsed = 0
  for (let day = firstDay; day <= lastDay; day += DAY_MS) {
    const cacheKey = `${timezone}:${day}`
    let window = windowCache.get(cacheKey)
    if (!window) {
      window = [utcForLocal(day, 9), utcForLocal(day, 21)]
      if (windowCache.size >= MAX_CACHED_WINDOWS) {
        windowCache.delete(windowCache.keys().next().value!)
      }
      windowCache.set(cacheKey, window)
    }
    const left = Math.max(startMs, window[0])
    const right = Math.min(endMs, window[1])
    elapsed += Math.max(0, right - left)
  }
  return elapsed / HOUR_MS
}
