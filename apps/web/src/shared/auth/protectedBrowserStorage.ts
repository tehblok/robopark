export const RECENT_ROBOTS_V2_STORAGE_PREFIX = 'robopark.recentRobots.v2.'
export const REPORT_DRAFT_STORAGE_PREFIX = 'robopark:report-draft:'

const DISPOSABLE_BROWSER_STORAGE_PREFIXES = [
  RECENT_ROBOTS_V2_STORAGE_PREFIX,
  'robopark:res:',
  'robopark:system-operation:',
] as const
const DISPOSABLE_BROWSER_STORAGE_KEYS = new Set(['robopark.recentRobots', 'robopark:system-operation', 'robopark:offline-identity:v1'])

function resolveStorage(storage?: Storage): Storage | null {
  if (storage) return storage
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function clearLegacyRecentRobots(storage?: Storage): void {
  const target = resolveStorage(storage)
  if (!target) return
  try { target.removeItem('robopark.recentRobots') } catch { /* Optional browser storage. */ }
}

export function clearProtectedBrowserStorage(storage?: Storage, options: { preserveOfflineIdentity?: boolean } = {}): void {
  const target = resolveStorage(storage)
  if (!target) return

  try {
    const keys = Array.from({ length: target.length }, (_, index) => target.key(index))
      .filter((key): key is string => key !== null)
      .filter((key) => DISPOSABLE_BROWSER_STORAGE_KEYS.has(key) || DISPOSABLE_BROWSER_STORAGE_PREFIXES.some(
        (prefix) => key.startsWith(prefix),
      ))
    for (const key of keys) {
      if (options.preserveOfflineIdentity && key === 'robopark:offline-identity:v1') continue
      target.removeItem(key)
    }
  } catch {
    // Unavailable browser storage must not block local auth fail-close.
  }
}
