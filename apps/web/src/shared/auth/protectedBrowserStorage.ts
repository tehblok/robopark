export const RECENT_ROBOTS_V2_STORAGE_PREFIX = 'robopark.recentRobots.v2.'
export const REPORT_DRAFT_STORAGE_PREFIX = 'robopark:report-draft:'

const PROTECTED_BROWSER_STORAGE_PREFIXES = [
  RECENT_ROBOTS_V2_STORAGE_PREFIX,
  REPORT_DRAFT_STORAGE_PREFIX,
] as const

function resolveStorage(storage?: Storage): Storage | null {
  if (storage) return storage
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function clearProtectedBrowserStorage(storage?: Storage): void {
  const target = resolveStorage(storage)
  if (!target) return

  try {
    const keys = Array.from({ length: target.length }, (_, index) => target.key(index))
      .filter((key): key is string => key !== null)
      .filter((key) => PROTECTED_BROWSER_STORAGE_PREFIXES.some(
        (prefix) => key.startsWith(prefix),
      ))
    for (const key of keys) target.removeItem(key)
  } catch {
    // Unavailable browser storage must not block local auth fail-close.
  }
}
