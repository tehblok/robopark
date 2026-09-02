import { afterEach, describe, expect, it } from 'vitest'
import { clearProtectedBrowserStorage } from './protectedBrowserStorage'

describe('clearProtectedBrowserStorage', () => {
  afterEach(() => localStorage.clear())

  it('removes every protected user and version namespace while preserving unrelated preferences', () => {
    const protectedEntries = [
      ['robopark.recentRobots.v2.3', 'old-recents'],
      ['robopark.recentRobots.v2.41', 'other-recents'],
      ['robopark:report-draft:v1:3:daily', 'old-draft'],
      ['robopark:report-draft:v2:41:blocker', 'other-draft'],
    ] as const
    const preservedEntries = [
      ['robopark-theme', 'dark'],
      ['robopark-density', 'compact'],
      ['robopark.recentRobots', '["447"]'],
      ['unrelated-key', 'keep-me'],
    ] as const
    for (const [key, value] of [...protectedEntries, ...preservedEntries]) {
      localStorage.setItem(key, value)
    }

    clearProtectedBrowserStorage(localStorage)

    for (const [key] of protectedEntries) expect(localStorage.getItem(key)).toBeNull()
    for (const [key, value] of preservedEntries) expect(localStorage.getItem(key)).toBe(value)
  })

  it('does not let unavailable or quota-limited storage break fail-close cleanup', () => {
    const unavailableStorage = {
      get length(): number {
        throw new DOMException('Storage unavailable', 'SecurityError')
      },
    } as Storage
    const quotaLimitedStorage = {
      length: 1,
      key: () => 'robopark:report-draft:v1:3:daily',
      removeItem: () => {
        throw new DOMException('Quota exceeded', 'QuotaExceededError')
      },
    } as unknown as Storage

    expect(() => clearProtectedBrowserStorage(unavailableStorage)).not.toThrow()
    expect(() => clearProtectedBrowserStorage(quotaLimitedStorage)).not.toThrow()
  })
})
