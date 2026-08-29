const STORAGE_KEY = 'robopark.recentRobots'
const MAX_RECENT = 6

function readStore(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
  } catch {
    return []
  }
}

export function loadRecentRobots(limit = MAX_RECENT): string[] {
  return readStore().slice(0, limit)
}

export function pushRecentRobot(query: string, limit = MAX_RECENT): string[] {
  const value = query.trim()
  if (!value) return loadRecentRobots(limit)
  const next = [value, ...readStore().filter((item) => item !== value)].slice(0, limit)
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    /* ignore quota / private mode */
  }
  return next
}

export function clearRecentRobots(): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore */
  }
}
