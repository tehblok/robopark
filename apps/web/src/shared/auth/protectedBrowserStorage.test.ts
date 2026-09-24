import { afterEach, describe, expect, it } from 'vitest'
import { clearProtectedBrowserStorage } from './protectedBrowserStorage'

describe('clearProtectedBrowserStorage', () => {
  afterEach(() => localStorage.clear())

  it('clears disposable snapshots while preserving unfinished drafts and preferences', () => {
    const disposableEntries = [
      ['robopark.recentRobots.v2.3', 'old-recents'],
      ['robopark.recentRobots.v2.41', 'other-recents'],
      ['robopark:res:old', 'cached'],
      ['robopark.recentRobots', '["447"]'],
    ] as const
    const protectedEntries = [
      ['robopark:report-draft:v1:3:daily', 'old-draft'],
      ['robopark:report-draft:v2:41:blocker', 'other-draft'],
      ['robopark:handoff:v1:3:task', 'handoff'],
      ['robopark:comment-draft:v1:3:task', 'comment'],
    ] as const
    const preservedEntries = [
      ['robopark-theme', 'dark'],
      ['robopark-density', 'compact'],
      ['unrelated-key', 'keep-me'],
    ] as const
    for (const [key, value] of [...disposableEntries, ...protectedEntries, ...preservedEntries]) {
      localStorage.setItem(key, value)
    }

    clearProtectedBrowserStorage(localStorage)

    for (const [key] of disposableEntries) expect(localStorage.getItem(key)).toBeNull()
    for (const [key, value] of protectedEntries) expect(localStorage.getItem(key)).toBe(value)
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
      key: () => 'robopark.recentRobots.v2.3',
      removeItem: () => {
        throw new DOMException('Quota exceeded', 'QuotaExceededError')
      },
    } as unknown as Storage

    expect(() => clearProtectedBrowserStorage(unavailableStorage)).not.toThrow()
    expect(() => clearProtectedBrowserStorage(quotaLimitedStorage)).not.toThrow()
  })
})
