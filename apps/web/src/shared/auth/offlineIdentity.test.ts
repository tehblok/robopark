import { afterEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import type { User } from '../../api'
import { clearProtectedBrowserStorage } from './protectedBrowserStorage'
import { flushDesktopOfflineIdentityForTests, hydrateDesktopOfflineIdentity, OFFLINE_IDENTITY_KEY, OFFLINE_IDENTITY_MAX_AGE_MS, readOfflineIdentity, writeOfflineIdentity } from './offlineIdentity'
import { resetDesktopStorageKeyForTests, setDesktopRuntimeForTests } from '../../pwa/desktopRuntime'
import { DesktopSessionRequiredError } from '../../pwa/desktopTransport'

const identity: User = { id: 7, username: 'offline-test', role: 'mechanic', access_status: 'approved',
  permissions: ['tracker.read'], parks: [{ id: 2, name: 'Test park', tag: 'test', timezone: 'Europe/Moscow', is_active: true }] }

afterEach(() => { localStorage.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); setDesktopRuntimeForTests(undefined); resetDesktopStorageKeyForTests() })

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
  it('stores the desktop offline identity encrypted and restores it before a cold start', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    const key = crypto.getRandomValues(new Uint8Array(32))
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ key: btoa(String.fromCharCode(...key)) }), { status: 200 })))
    writeOfflineIdentity({ ...identity, token: 'never-persist' } as User)
    await flushDesktopOfflineIdentityForTests()
    expect(localStorage.getItem(OFFLINE_IDENTITY_KEY)).toBeNull()
    await hydrateDesktopOfflineIdentity()
    expect(readOfflineIdentity()).toMatchObject(identity)
  })
  it('reports protected desktop identity failures to the bounded startup gate', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    const key = crypto.getRandomValues(new Uint8Array(32))
    const fetchKey = vi.fn(async () => new Response(JSON.stringify({ key: btoa(String.fromCharCode(...key)) }), { status: 200 }))
    vi.stubGlobal('fetch', fetchKey)
    writeOfflineIdentity(identity)
    await flushDesktopOfflineIdentityForTests()
    resetDesktopStorageKeyForTests()
    fetchKey.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'local_session_required' }), { status: 401 }))

    await expect(hydrateDesktopOfflineIdentity({ propagateFailure: true })).rejects.toThrow('desktop_storage_key_unavailable')
    expect(readOfflineIdentity()).toBeNull()
  })
  it('keeps an encrypted snapshot when a direct tab lacks native capability', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    const key = crypto.getRandomValues(new Uint8Array(32))
    const encodedKey = JSON.stringify({ key: btoa(String.fromCharCode(...key)) })
    const fetchKey = vi.fn(async () => new Response(encodedKey, { status: 200 }))
    vi.stubGlobal('fetch', fetchKey)
    writeOfflineIdentity(identity)
    await flushDesktopOfflineIdentityForTests()
    resetDesktopStorageKeyForTests()
    fetchKey.mockRejectedValueOnce(new DesktopSessionRequiredError())

    await expect(hydrateDesktopOfflineIdentity({ propagateFailure: true })).rejects.toBeInstanceOf(DesktopSessionRequiredError)
    expect(readOfflineIdentity()).toBeNull()

    resetDesktopStorageKeyForTests()
    await hydrateDesktopOfflineIdentity({ propagateFailure: true })
    expect(readOfflineIdentity()).toMatchObject(identity)
  })
  it('does not hard-block repeated startup on a malformed protected snapshot', async () => {
    const indexedDB = new IDBFactory()
    vi.stubGlobal('indexedDB', indexedDB)
    const instanceId = '6f17d720-9fab-48da-8dd5-9f3d270b2878'
    setDesktopRuntimeForTests({ version: 1, instanceId })
    const request = indexedDB.open('robopark-native-identity', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('identity')
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    const transaction = db.transaction('identity', 'readwrite')
    transaction.objectStore('identity').put({ broken: true }, instanceId)
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve()
      transaction.onabort = transaction.onerror = () => reject(transaction.error)
    })
    db.close()

    await expect(hydrateDesktopOfflineIdentity({ propagateFailure: true })).resolves.toBeUndefined()
    await expect(hydrateDesktopOfflineIdentity({ propagateFailure: true })).resolves.toBeUndefined()
    expect(readOfflineIdentity()).toBeNull()
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
