import { afterEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../../api'
import { clearProtectedBrowserStorage } from './protectedBrowserStorage'
import { OFFLINE_IDENTITY_KEY, OFFLINE_IDENTITY_MAX_AGE_MS, readOfflineIdentity, writeOfflineIdentity } from './offlineIdentity'

const identity: User = { id: 7, username: 'offline-test', role: 'mechanic', access_status: 'approved',
  permissions: ['tracker.read'], parks: [{ id: 2, name: 'Test park', tag: 'test', timezone: 'Europe/Moscow', is_active: true }] }

afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); vi.useRealTimers() })

describe('previously verified offline identity', () => {
  it.each([{ timezone: 'Invalid/Timezone' }, { tracker_queue: {} }, { is_active: 'yes' }])('rejects invalid cached park metadata', patch => {
    localStorage.setItem(OFFLINE_IDENTITY_KEY, JSON.stringify({ version: 1, verifiedAt: Date.now(), user: {
      ...identity, parks: [{ ...identity.parks[0], ...patch }],
    } }))
    expect(readOfflineIdentity()).toBeNull()
  })
  it('whitelists identity metadata without credentials or unrelated fields', () => {
    writeOfflineIdentity({ ...identity, token: 'must-not-persist', password: 'must-not-persist' } as User)
    expect(localStorage.getItem(OFFLINE_IDENTITY_KEY)).not.toContain('must-not-persist')
    expect(readOfflineIdentity()).toMatchObject(identity)
  })
  it('expires after 72 hours, and offline reads never renew verification', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-30T09:00:00Z'))
    writeOfflineIdentity(identity)
    const original = localStorage.getItem(OFFLINE_IDENTITY_KEY)
    vi.advanceTimersByTime(OFFLINE_IDENTITY_MAX_AGE_MS - 1)
    expect(readOfflineIdentity()?.id).toBe(7)
    expect(localStorage.getItem(OFFLINE_IDENTITY_KEY)).toBe(original)
    vi.advanceTimersByTime(1)
    expect(readOfflineIdentity()).toBeNull()
  })
  it.each([{ ...identity, access_status: 'pending' }, { ...identity, must_change_password: true }])('refuses an unapproved or password-change identity', user => {
    writeOfflineIdentity(identity)
    writeOfflineIdentity(user as User)
    expect(readOfflineIdentity()).toBeNull()
  })
  it('cannot restore after logout cleanup', () => {
    writeOfflineIdentity(identity)
    clearProtectedBrowserStorage()
    expect(readOfflineIdentity()).toBeNull()
  })
  it.each(['bad json', JSON.stringify({ version: 1, verifiedAt: Date.now() + 3600000, user: identity }), 'x'.repeat(65537)])('rejects malformed, future-dated or oversized storage', raw => {
    localStorage.setItem(OFFLINE_IDENTITY_KEY, raw)
    expect(readOfflineIdentity()).toBeNull()
  })
  it('tolerates storage denied by the browser', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    expect(() => writeOfflineIdentity(identity)).not.toThrow()
    expect(readOfflineIdentity()).toBeNull()
  })
})
