import { RECENT_ROBOTS_V2_STORAGE_PREFIX } from '../../shared/auth/protectedBrowserStorage'

const MAX_RECENT_ROBOTS = 6
export const RECENT_ROBOT_TTL_MS = 2_592_000_000

export type RecentRobot = {
  query: string
  vin: string
  openedAt: number
}

type ResolvedRobot = Pick<RecentRobot, 'query' | 'vin'>

function storageKey(userId: number): string {
  return `${RECENT_ROBOTS_V2_STORAGE_PREFIX}${userId}`
}

function resolveStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function isRecentRobot(value: unknown): value is RecentRobot {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return typeof item.query === 'string'
    && item.query.trim().length > 0
    && typeof item.vin === 'string'
    && item.vin.trim().length > 0
    && typeof item.openedAt === 'number'
    && Number.isFinite(item.openedAt)
    && Number.isFinite(new Date(item.openedAt).getTime())
}

function normalize(item: RecentRobot): RecentRobot {
  return {
    query: item.query.trim(),
    vin: item.vin.trim().toUpperCase(),
    openedAt: item.openedAt,
  }
}

function validRecent(items: readonly RecentRobot[], now: number): RecentRobot[] {
  const seen = new Set<string>()
  return items
    .map(normalize)
    .filter((item) => now - item.openedAt < RECENT_ROBOT_TTL_MS)
    .sort((left, right) => right.openedAt - left.openedAt)
    .filter((item) => {
      if (seen.has(item.vin)) return false
      seen.add(item.vin)
      return true
    })
    .slice(0, MAX_RECENT_ROBOTS)
}

function read(userId: number): RecentRobot[] {
  const storage = resolveStorage()
  if (!storage) return []
  try {
    const raw = storage.getItem(storageKey(userId))
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(isRecentRobot) : []
  } catch {
    return []
  }
}

function write(userId: number, items: RecentRobot[]): void {
  const storage = resolveStorage()
  if (!storage) return
  try {
    storage.setItem(storageKey(userId), JSON.stringify(items))
  } catch {
    // Storage availability must not affect identity resolution.
  }
}

export function loadRecentRobots(userId: number, now = Date.now()): RecentRobot[] {
  const items = validRecent(read(userId), now)
  write(userId, items)
  return items
}

export function rememberRobot(userId: number, resolved: ResolvedRobot, now = Date.now()): void {
  const candidate: RecentRobot = {
    query: resolved.query,
    vin: resolved.vin,
    openedAt: now,
  }
  if (!isRecentRobot(candidate)) return
  write(userId, validRecent([candidate, ...read(userId)], now))
}

export function clearRecentRobots(userId: number): void {
  const storage = resolveStorage()
  if (!storage) return
  try {
    storage.removeItem(storageKey(userId))
  } catch {
    // Storage availability must not affect navigation.
  }
}
