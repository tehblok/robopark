import { act, render, screen, waitFor } from '@testing-library/react'
import { StrictMode, useLayoutEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, ApiTimeoutError, api, type User } from './api'
import { AuthProvider } from './auth'
import { type AuthContextValue, useAuth } from './auth-context'
import { resourceStore } from './lib/resource'
import { IDBFactory } from 'fake-indexeddb'
import { openOfflineDb, purgeOfflineScope } from './pwa/offlineDb'
import { offlineScopeForUser } from './lib/deviceResourceCache'
import * as deviceCache from './lib/deviceResourceCache'
import { storageRegistry } from './pwa/storageRegistry'
import { openShareTargetInbox } from './pwa/shareTargetStore'
import { registerServiceWorker } from './pwa/registerServiceWorker'
import { operationReservationKey } from './domains/system/operationReservation'
import { clearProtectedBrowserStorage } from './shared/auth/protectedBrowserStorage'
import { readReportPhotoDraft, writeReportPhotoDraft } from './domains/reports/reportPhotoDrafts'
import { reportsAccessIdentity } from './domains/reports/reports'

const oldAccount: User = {
  id: 3,
  username: 'old-account',
  role: 'operator',
  access_status: 'approved',
  permissions: ['nav.overview'],
  parks: [],
}

const replacementAccount: User = {
  id: 3,
  username: 'replacement-account',
  role: 'operator',
  access_status: 'approved',
  permissions: ['nav.overview'],
  parks: [],
}

const recentKey = 'robopark.recentRobots.v2.3'
const otherRecentKey = 'robopark.recentRobots.v2.41'
const reportKey = 'robopark:report-draft:v1:3:daily'
const otherReportKey = 'robopark:report-draft:v2:41:blocker'
const legacyRecentKey = 'robopark.recentRobots'
const resourceKey = 'protected:test'
const operationKey = operationReservationKey(oldAccount)

let currentAuth: AuthContextValue | null = null

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function AuthProbe() {
  const auth = useAuth()
  // Keep assertions on the context synchronized with the committed DOM;
  // passive effects can still expose the previous loading snapshot.
  useLayoutEffect(() => {
    currentAuth = auth
    return () => {
      currentAuth = null
    }
  }, [auth])
  return <output>{auth.loading ? 'loading' : auth.user?.username ?? 'anonymous'}</output>
}

function seedProtectedState(): void {
  resourceStore.set(resourceKey, { secret: 'cached' }, false)
  localStorage.setItem(recentKey, 'old-recents')
  localStorage.setItem(otherRecentKey, 'other-recents')
  localStorage.setItem(reportKey, 'old-draft')
  localStorage.setItem(otherReportKey, 'other-draft')
  localStorage.setItem(legacyRecentKey, '["447"]')
  localStorage.setItem(operationKey, '{"id":"private-operation"}')
  localStorage.setItem('robopark-theme', 'dark')
  localStorage.setItem('robopark-density', 'compact')
  localStorage.setItem('unrelated-key', 'keep-me')
}

function expectProtectedStateCleared(): void {
  expect(resourceStore.get(resourceKey)).toBeUndefined()
  expect(localStorage.getItem(recentKey)).toBeNull()
  expect(localStorage.getItem(otherRecentKey)).toBeNull()
  expect(localStorage.getItem(reportKey)).toBe('old-draft')
  expect(localStorage.getItem(otherReportKey)).toBe('other-draft')
  expect(localStorage.getItem(legacyRecentKey)).toBeNull()
  expect(localStorage.getItem(operationKey)).toBeNull()
  expect(localStorage.getItem('robopark-theme')).toBe('dark')
  expect(localStorage.getItem('robopark-density')).toBe('compact')
  expect(localStorage.getItem('unrelated-key')).toBe('keep-me')
}

function expectProtectedStateRetained(): void {
  expect(resourceStore.get(resourceKey)).toEqual({ secret: 'cached' })
  expect(localStorage.getItem(recentKey)).toBe('old-recents')
  expect(localStorage.getItem(otherRecentKey)).toBe('other-recents')
  expect(localStorage.getItem(reportKey)).toBe('old-draft')
  expect(localStorage.getItem(otherReportKey)).toBe('other-draft')
  expect(localStorage.getItem(operationKey)).toBe('{"id":"private-operation"}')
}

async function activationSafetyProbe(): Promise<() => boolean> {
  const listeners = new Map<string, (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void, onmessage?: (event: { data?: unknown }) => void }[] }) => void>()
  await registerServiceWorker({
    production: true, secure: true,
    serviceWorker: {
      register: async () => ({ update: async () => undefined }),
      addEventListener: (name, listener) => listeners.set(name, listener),
    },
  })
  return () => {
    let safe = false
    const port = { postMessage: (reply: unknown) => {
      const next = (reply as { safe?: unknown }).safe
      if (typeof next === 'boolean') safe = next
    }, onmessage: undefined as undefined | ((event: { data?: unknown }) => void) }
    listeners.get('message')?.({ data: { type: 'PREPARE_ACTIVATION' }, ports: [port] })
    port.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    return safe
  }
}

describe('AuthProvider session boundaries', () => {
  it('recovers claimed share cleanup after logout already removed the offline identity', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    const park = { id: 7, name: 'Recovery park', tag: 'recovery', timezone: 'Europe/Moscow' }
    const owner = { ...oldAccount, parks: [park] }
    const photoKey = 'robopark:report-draft:3:7:recovery'
    await writeReportPhotoDraft({ key: photoKey, ownerKey: reportsAccessIdentity(owner, park), revision: 'recovery', activeForm: 'problem', trackerKey: '', title: 'Repair photo', body: '', createdReportId: null, attachmentKind: 'device_photo', attachment: { blob: new Blob(['private']), name: 'private.jpg', lastModified: 1 } })
    const inbox = await openShareTargetInbox()
    for (const id of ['claimed', 'unclaimed']) await inbox.save({ id, createdAt: Date.now(), name: 'private.jpg', type: 'image/jpeg', blob: new Blob(['private']), assignment: null })
    await inbox.claim('claimed', oldAccount.id)
    localStorage.setItem('robopark:local-signout:v1', JSON.stringify({ accountId: owner.id, scope: offlineScopeForUser(owner, 'all'), parks: ['all', '7'] }))
    clearProtectedBrowserStorage()
    vi.spyOn(api, 'me').mockResolvedValue(oldAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('anonymous')
    expect(await inbox.list(oldAccount.id)).toEqual([])
    expect(await readReportPhotoDraft(photoKey)).toBeNull()
    expect(await inbox.claim('unclaimed', 41)).toMatchObject({ id: 'unclaimed', ownerAccountId: 41 })
    inbox.close?.()
  })
  it('finishes interrupted local logout cleanup and permits safe activation while signed out', async () => {
    const safeToActivate = await activationSafetyProbe()
    localStorage.setItem('robopark:local-signout:v1', '1')
    seedProtectedState()
    const me = vi.spyOn(api, 'me')
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('anonymous')
    expect(me).not.toHaveBeenCalled()
    expectProtectedStateCleared()
    expect(safeToActivate()).toBe(true)
  })
  it('keeps identity after a scoped 403 but drops denied resource data before an offline restart', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    resourceStore.set('denied-resource', { forbidden: true }, false)
    act(() => window.dispatchEvent(new CustomEvent('robopark:authorization-failure', { detail: { status: 403 } })))
    expect(resourceStore.get('denied-resource')).toBeUndefined()
    first.unmount()
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeVisible()
  })
  it('verifies the changed cookie before adopting another tab account', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValueOnce(replacementAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'robopark:auth-transition', newValue: JSON.stringify({ phase: 'verified', nonce: 'test-transition' }) })))
    expect(await screen.findByText('replacement-account')).toBeVisible()
  })
  it('retries another tab verification on reconnect after a transient failure', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValueOnce(new TypeError('Failed to fetch')).mockResolvedValueOnce(replacementAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'robopark:auth-transition', newValue: JSON.stringify({ phase: 'verified', nonce: 'retry-transition' }) })))
    await screen.findByText('anonymous')
    act(() => window.dispatchEvent(new Event('online')))
    expect(await screen.findByText('replacement-account')).toBeVisible()
  })
  it('invalidates the old tab identity when another tab changes authentication', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'robopark:auth-transition', newValue: 'another-tab-transition' })))
    expect(await screen.findByText('anonymous')).toBeVisible()
  })
  it.each([new ApiError(503), new SyntaxError('Invalid JSON')])('retains a confirmed identity through a transient proxy or parse failure', async failure => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValueOnce(failure)
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    first.unmount()
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeVisible()
  })
  it('restores a previously confirmed identity after an offline cold start without logging in again', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    first.unmount()
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeVisible()
    expect(currentAuth!.offlineSession).toBe(true)
    vi.mocked(api.me).mockResolvedValueOnce(oldAccount)
    await act(async () => { await currentAuth!.refreshUser() })
    expect(currentAuth!.offlineSession).toBe(false)
  })

  it.each([false, true])('settles a disconnected cold start immediately (saved identity: %s) and revalidates on reconnect', async saved => {
    const me = vi.spyOn(api, 'me').mockResolvedValue(oldAccount)
    if (saved) {
      const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
      await screen.findByText('old-account')
      first.unmount()
    }
    me.mockClear().mockImplementation(() => new Promise(() => {}))
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText(saved ? 'old-account' : 'anonymous')).toBeVisible()
    expect(me).not.toHaveBeenCalled()
    expect(currentAuth!.offlineSession).toBe(saved)
    me.mockRejectedValue(new ApiError(401))
    await act(async () => window.dispatchEvent(new Event('online')))
    await waitFor(() => expect(me).toHaveBeenCalledTimes(1))
    expect(await screen.findByText('anonymous')).toBeVisible()
  })

  it('clears revoked scopes when reconnect overlaps offline cache activation', async () => {
    const revoked = { ...oldAccount, username: 'restricted-account', permissions: [] }
    const me = vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValue(revoked)
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    first.unmount()
    seedProtectedState()
    const bootCache = deferred<void>()
    const verifiedCache = deferred<void>()
    const activate = vi.spyOn(deviceCache, 'activateDeviceResourceCache')
      .mockImplementationOnce(() => bootCache.promise)
      .mockImplementationOnce(() => verifiedCache.promise)
    const purge = vi.spyOn(storageRegistry, 'purgeScope')
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await waitFor(() => expect(activate).toHaveBeenCalledTimes(1))
    await act(async () => { window.dispatchEvent(new Event('online')) })
    await waitFor(() => expect(me).toHaveBeenCalledTimes(2))
    await act(async () => { bootCache.resolve() })
    await waitFor(() => expect(activate).toHaveBeenCalledTimes(2))
    await act(async () => { verifiedCache.resolve() })
    expect(await screen.findByText('restricted-account')).toBeVisible()
    expect(me).toHaveBeenCalledTimes(2)
    expect(purge).toHaveBeenCalledWith(offlineScopeForUser(oldAccount, 'all'))
    expect(resourceStore.get(resourceKey)).toBeUndefined()
    expect(localStorage.getItem(operationKey)).toBeNull()
    expect(currentAuth!.offlineSession).toBe(false)
  })

  it('activates saved cache before publishing a transient reconnect fallback', async () => {
    const me = vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValue(new ApiError(503))
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    first.unmount()
    const bootCache = deferred<void>()
    const fallbackCache = deferred<void>()
    const activate = vi.spyOn(deviceCache, 'activateDeviceResourceCache')
      .mockImplementationOnce(() => bootCache.promise)
      .mockImplementationOnce(() => fallbackCache.promise)
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await act(async () => { window.dispatchEvent(new Event('online')) })
    await waitFor(() => expect(me).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(activate).toHaveBeenCalledTimes(2))
    expect(screen.getByText('loading')).toBeVisible()
    await act(async () => { bootCache.resolve() })
    expect(screen.getByText('loading')).toBeVisible()
    await act(async () => { fallbackCache.resolve() })
    expect(await screen.findByText('old-account')).toBeVisible()
    expect(currentAuth!.offlineSession).toBe(true)
  })

  it('applies reconnect revocation before a blocked offline bootstrap settles', async () => {
    const me = vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValue(new ApiError(401))
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    first.unmount()
    const bootCache = deferred<void>()
    vi.spyOn(deviceCache, 'activateDeviceResourceCache').mockImplementationOnce(() => bootCache.promise)
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await act(async () => { window.dispatchEvent(new Event('online')) })
    await waitFor(() => expect(me).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('anonymous')).toBeVisible()
    await act(async () => { bootCache.resolve() })
    expect(screen.getByText('anonymous')).toBeVisible()
    expect(currentAuth!.user).toBeNull()
  })

  it('does not restore an offline identity after an authoritative session denial', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValueOnce(new ApiError(401)).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    first.unmount()
    const denied = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('anonymous')
    denied.unmount()
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('anonymous')).toBeVisible()
  })

  it('rechecks the restored session when connectivity returns and applies server revocation', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockRejectedValueOnce(new TypeError('Failed to fetch')).mockRejectedValueOnce(new ApiError(401))
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    first.unmount()
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    act(() => window.dispatchEvent(new Event('online')))
    expect(await screen.findByText('anonymous')).toBeVisible()
  })
  afterEach(async () => {
    await purgeOfflineScope()
    currentAuth = null
    resourceStore.clearAll()
    localStorage.clear()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('reclaims retired share storage even when the device is signed out', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    const request = indexedDB.open('robopark-share-inbox', 1)
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    db.close()
    localStorage.setItem('robopark:legacy-share-inbox-retired-at', String(Date.now() - 25 * 60 * 60 * 1000))
    vi.spyOn(api, 'me').mockRejectedValue(new ApiError(401, 'unauthorized'))

    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('anonymous')
    await waitFor(async () => {
      expect(await indexedDB.databases()).not.toContainEqual(expect.objectContaining({ name: 'robopark-share-inbox' }))
    })
  })

  it('removes legacy private resource snapshots when an existing session resumes', async () => {
    const oldKey = 'robopark:res:work:7:issue:OLD-1'
    const newKey = 'robopark:res:work:7:issue:NEW-1'
    localStorage.setItem(oldKey, JSON.stringify({ v: 1, updatedAt: Date.now(), data: { secret: 'old' } }))
    localStorage.setItem(newKey, JSON.stringify({ v: 2, updatedAt: Date.now(), data: { secret: 'new' } }))
    vi.spyOn(api, 'me').mockResolvedValue(oldAccount)

    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')

    expect(localStorage.getItem(oldKey)).toBeNull()
    expect(localStorage.getItem(newKey)).toBeNull()
  })

  it('purges provenance-free legacy robot recents before a successful bootstrap publishes a user', async () => {
    localStorage.setItem(legacyRecentKey, '["447"]')
    localStorage.setItem(recentKey, '["owned"]')
    vi.spyOn(api, 'me').mockResolvedValue(replacementAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('replacement-account')
    expect(localStorage.getItem(legacyRecentKey)).toBeNull()
    expect(localStorage.getItem(recentKey)).toBe('["owned"]')
  })

  it('keeps the auth refresh callback stable across a same-user refresh', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValueOnce({ ...oldAccount })
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    const refresh = currentAuth!.refreshUser

    await act(async () => { await refresh() })

    expect(currentAuth!.refreshUser).toBe(refresh)
  })

  it('drops in-memory protected resources when a refresh changes permissions', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount)
      .mockResolvedValueOnce({ ...oldAccount, permissions: [] })
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    resourceStore.set('operator:parks', [{ id: 7, name: 'previous access' }], false)

    await act(async () => { await currentAuth!.refreshUser() })

    expect(resourceStore.get('operator:parks')).toBeUndefined()
  })

  it('drops protected resources when account approval is revoked', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount)
      .mockResolvedValueOnce({ ...oldAccount, access_status: 'rejected' })
      .mockResolvedValueOnce(oldAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    resourceStore.set('operator:parks', [{ id: 7, name: 'approved-only' }], false)
    localStorage.setItem('robopark:report-draft:3:all', 'unfinished')

    await act(async () => { await currentAuth!.refreshUser() })

    expect(resourceStore.get('operator:parks')).toBeUndefined()
    expect(localStorage.getItem('robopark:report-draft:3:all')).toBeNull()
    await act(async () => { await currentAuth!.refreshUser() })
    expect(localStorage.getItem('robopark:report-draft:3:all')).toBe('unfinished')
  })

  it('retains warm resources when a refresh confirms the same authorization', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValueOnce({ ...oldAccount })
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    resourceStore.set('operator:parks', [{ id: 7, name: 'current access' }], false)

    await act(async () => { await currentAuth!.refreshUser() })

    expect(resourceStore.get('operator:parks')).toEqual([{ id: 7, name: 'current access' }])
  })

  it('purges the previous account share-target photo when refresh replaces the account', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValueOnce(replacementAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'unclaimed', createdAt: Date.now(), name: 'private.jpg', type: 'image/jpeg', blob: new Blob(['private']), assignment: null })
    await inbox.claim('unclaimed', oldAccount.id)
    await act(async () => { await currentAuth!.refreshUser() })
    expect(await inbox.list(oldAccount.id)).toEqual([])
    inbox.close?.()
  })

  it('keeps a newly shared photo through login until this account claims it', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    vi.spyOn(api, 'me').mockRejectedValueOnce(new ApiError(401)).mockResolvedValueOnce(oldAccount)
    vi.spyOn(api, 'login').mockResolvedValue(undefined)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('anonymous')
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'fresh-share', createdAt: Date.now(), name: 'new-photo.jpg', type: 'image/jpeg',
      blob: new Blob(['photo'], { type: 'image/jpeg' }), assignment: null, ownerAccountId: null })

    await act(async () => { await currentAuth!.login('old-account', 'password') })
    expect(await inbox.claim('fresh-share', oldAccount.id)).toMatchObject({ name: 'new-photo.jpg', ownerAccountId: oldAccount.id })
    inbox.close?.()
  })

  it.each(['success', '401'] as const)('ignores an old refresh %s after a replacement login', async (result) => {
    const pending = deferred<User>()
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount)
      .mockImplementationOnce(() => pending.promise).mockResolvedValueOnce(replacementAccount)
    vi.spyOn(api, 'login').mockResolvedValueOnce(undefined)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    const refresh = currentAuth!.refreshUser().catch(() => undefined)
    await act(async () => { await currentAuth!.login('replacement-account', 'password') })
    seedProtectedState()

    await act(async () => {
      if (result === 'success') pending.resolve(oldAccount)
      else pending.reject(new ApiError(401))
      await refresh
    })

    expect(screen.getByText('replacement-account')).toBeInTheDocument()
    expectProtectedStateRetained()
  })

  it('does not republish an old refresh after logout completed', async () => {
    const pending = deferred<User>()
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockImplementationOnce(() => pending.promise)
    vi.spyOn(api, 'logout').mockResolvedValueOnce(undefined)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    const refresh = currentAuth!.refreshUser()
    await act(async () => { await currentAuth!.logout() })
    await act(async () => { pending.resolve(oldAccount); await refresh })

    expect(screen.getByText('anonymous')).toBeInTheDocument()
  })

  it('quarantines a scoped report draft on logout and restores it only after exact reauthorization', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    vi.spyOn(api, 'me').mockResolvedValue(oldAccount)
    vi.spyOn(api, 'logout').mockResolvedValue(undefined)
    vi.spyOn(api, 'login').mockResolvedValue(undefined)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    await openOfflineDb(offlineScopeForUser(oldAccount, 'all'))
    localStorage.setItem('robopark:report-draft:3:all', 'unfinished')
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'unclaimed', createdAt: Date.now(), name: 'photo.jpg', type: 'image/jpeg', blob: new Blob(['photo']), assignment: null })
    await inbox.claim('unclaimed', oldAccount.id)

    await act(async () => { await currentAuth!.logout() })
    expect(localStorage.getItem('robopark:report-draft:3:all')).toBeNull()
    expect(await inbox.list(oldAccount.id)).toEqual([])
    inbox.close?.()
    await act(async () => { await currentAuth!.login('old-account', 'password') })
    expect(localStorage.getItem('robopark:report-draft:3:all')).toBe('unfinished')
  })

  it('hides pending local work when an account loses its previous role', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    const manager = { ...oldAccount, role: 'manager', permissions: ['nav.overview', 'admin.users'] }
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValueOnce(manager).mockResolvedValueOnce(oldAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    await openOfflineDb(offlineScopeForUser(oldAccount, 'all'))
    localStorage.setItem('robopark:report-draft:3:all', 'unfinished')

    await act(async () => { await currentAuth!.refreshUser() })
    expect(localStorage.getItem('robopark:report-draft:3:all')).toBeNull()
    await act(async () => { await currentAuth!.refreshUser() })
    expect(localStorage.getItem('robopark:report-draft:3:all')).toBe('unfinished')
  })

  it('quarantines drafts at logout even when offline IndexedDB was unavailable', async () => {
    vi.stubGlobal('indexedDB', undefined)
    vi.spyOn(api, 'me').mockResolvedValue(oldAccount)
    vi.spyOn(api, 'logout').mockResolvedValue(undefined)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    localStorage.setItem('robopark:report-draft:3:all', 'unfinished')

    await act(async () => { await currentAuth!.logout() })

    expect(localStorage.getItem('robopark:report-draft:3:all')).toBeNull()
  })

  it.each(['bootstrap', 'login'] as const)('does not publish an old %s response over a replacement login', async (origin) => {
    const pending = deferred<User>()
    const me = vi.spyOn(api, 'me')
    vi.spyOn(api, 'login').mockResolvedValue(undefined)
    if (origin === 'login') me.mockResolvedValueOnce(oldAccount)
    me.mockImplementationOnce(() => pending.promise).mockResolvedValueOnce(replacementAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    let oldLogin: Promise<unknown> | undefined
    if (origin === 'login') {
      await screen.findByText('old-account')
      await act(async () => { oldLogin = currentAuth!.login('old-account', 'password').catch(() => undefined) })
    }
    await act(async () => { await currentAuth!.login('replacement-account', 'password') })
    await act(async () => { pending.resolve(oldAccount); await oldLogin })

    expect(screen.getByText('replacement-account')).toBeInTheDocument()
  })

  it('does not let a pending logout clear the next login or its protected state', async () => {
    const pending = deferred<void>()
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValueOnce(replacementAccount)
    vi.spyOn(api, 'logout').mockImplementationOnce(() => pending.promise)
    vi.spyOn(api, 'login').mockResolvedValueOnce(undefined)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    let logout!: Promise<void>
    act(() => { logout = currentAuth!.logout() })
    await act(async () => { await currentAuth!.login('replacement-account', 'password') })
    seedProtectedState()
    await act(async () => { pending.resolve(); await logout })

    expect(screen.getByText('replacement-account')).toBeInTheDocument()
    expectProtectedStateRetained()
  })

  it('ignores a superseded StrictMode bootstrap denial', async () => {
    const first = deferred<User>()
    vi.spyOn(api, 'me').mockImplementationOnce(() => first.promise).mockResolvedValueOnce(replacementAccount)
    render(<StrictMode><AuthProvider><AuthProbe /></AuthProvider></StrictMode>)
    await screen.findByText('replacement-account')
    seedProtectedState()
    await act(async () => { first.reject(new ApiError(401)) })

    expect(screen.getByText('replacement-account')).toBeInTheDocument()
    expectProtectedStateRetained()
  })

  it('clears protected state when the initial session request returns 401', async () => {
    vi.spyOn(api, 'me').mockRejectedValueOnce(new ApiError(401))
    seedProtectedState()

    render(<AuthProvider><AuthProbe /></AuthProvider>)

    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expectProtectedStateCleared()
  })

  it('allows an update after an authoritative anonymous 401', async () => {
    const safeToActivate = await activationSafetyProbe()
    vi.spyOn(api, 'me').mockRejectedValueOnce(new ApiError(401))
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expect(safeToActivate()).toBe(true)
  })

  it.each([
    ['network', new TypeError('Failed to fetch')],
    ['server', new ApiError(503, 'unavailable')],
    ['parse', new SyntaxError('invalid JSON')],
    ['timeout', new ApiTimeoutError(30_000)],
  ])('keeps update safety unknown after an initial %s identity failure', async (_label, failure) => {
    const safeToActivate = await activationSafetyProbe()
    vi.spyOn(api, 'me').mockRejectedValueOnce(failure)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expect(safeToActivate()).toBe(false)
  })

  it('purges protected memory and account data when the emergency query POST returns 403', async () => {
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeInTheDocument()
    seedProtectedState()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'forbidden' }),
      { status: 403, headers: { 'Content-Type': 'application/json' } },
    )))

    await act(async () => {
      await expect(api.emergencyResolve('447')).rejects.toMatchObject({ status: 403 })
    })

    expect(screen.getByText('old-account')).toBeInTheDocument()
    expectProtectedStateCleared()
    vi.unstubAllGlobals()
  })

  it('clears the authenticated session before refreshUser rethrows a 401', async () => {
    const denial = new ApiError(401, 'session_expired')
    vi.spyOn(api, 'me')
      .mockResolvedValueOnce(oldAccount)
      .mockRejectedValueOnce(denial)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeInTheDocument()
    seedProtectedState()

    await act(async () => {
      await expect(currentAuth!.refreshUser()).rejects.toBe(denial)
    })

    expect(screen.getByText('anonymous')).toBeInTheDocument()
    expectProtectedStateCleared()
  })

  it('clears protected state in logout finally when the network request fails', async () => {
    const networkFailure = new TypeError('Failed to fetch')
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount)
    vi.spyOn(api, 'logout').mockRejectedValueOnce(networkFailure)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeInTheDocument()
    seedProtectedState()

    await act(async () => {
      await expect(currentAuth!.logout()).rejects.toBe(networkFailure)
    })

    expect(screen.getByText('anonymous')).toBeInTheDocument()
    expectProtectedStateCleared()
  })

  it('does not silently sign back in after offline logout when the server cookie is still valid', async () => {
    vi.spyOn(api, 'me').mockResolvedValue(oldAccount)
    vi.spyOn(api, 'logout').mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    await act(async () => { await expect(currentAuth!.logout()).rejects.toBeInstanceOf(TypeError) })
    first.unmount()
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('anonymous')).toBeVisible()
    expect(api.me).toHaveBeenCalledTimes(1)
    vi.spyOn(api, 'login').mockResolvedValueOnce(undefined)
    await act(async () => { await currentAuth!.login('old-account', 'test-only-password') })
    expect(screen.getByText('old-account')).toBeVisible()
  })

  it('prevents a replacement account with a reused numeric ID from inheriting stale payloads', async () => {
    vi.spyOn(api, 'me')
      .mockResolvedValueOnce(oldAccount)
      .mockResolvedValueOnce(replacementAccount)
    vi.spyOn(api, 'logout').mockResolvedValueOnce(undefined)
    const login = vi.spyOn(api, 'login').mockImplementationOnce(async () => {
      expect(localStorage.getItem(recentKey)).toBeNull()
      expect(localStorage.getItem(reportKey)).toBe('late-old-draft')
    })
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeInTheDocument()
    seedProtectedState()

    await act(async () => currentAuth!.logout())
    expect(screen.getByText('anonymous')).toBeInTheDocument()
    expectProtectedStateCleared()

    // Model a stale late browser write after the old session ended. Login is
    // the second independent boundary and must clear it before making a request.
    localStorage.setItem(recentKey, 'late-old-recents')
    localStorage.setItem(reportKey, 'late-old-draft')
    await act(async () => currentAuth!.login('replacement-account', 'password'))

    expect(login).toHaveBeenCalledTimes(1)
    expect(screen.getByText('replacement-account')).toBeInTheDocument()
    expect(localStorage.getItem(recentKey)).toBeNull()
    expect(localStorage.getItem(reportKey)).toBe('late-old-draft')
  })

  it.each([
    ['offline', new TypeError('Failed to fetch')],
    ['timeout', new ApiTimeoutError(30_000)],
    ['server', new ApiError(503, 'tracker_upstream_error')],
    ['rate limit', new ApiError(429, null, undefined, 60_000)],
  ])('retains same-session state after a transient %s refresh failure', async (_label, failure) => {
    vi.spyOn(api, 'me')
      .mockResolvedValueOnce(oldAccount)
      .mockRejectedValueOnce(failure)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeInTheDocument()
    seedProtectedState()

    await act(async () => {
      await expect(currentAuth!.refreshUser()).rejects.toBe(failure)
    })

    expect(screen.getByText('old-account')).toBeInTheDocument()
    expectProtectedStateRetained()
  })

  it('retains same-session state across successful ordinary reloads', async () => {
    const me = vi.spyOn(api, 'me').mockResolvedValue(oldAccount)
    seedProtectedState()

    const firstRender = render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeInTheDocument()
    firstRender.unmount()
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('old-account')).toBeInTheDocument()

    expect(me).toHaveBeenCalledTimes(2)
    expectProtectedStateRetained()
  })
})

it('reconnects a Royal page using only read-only identity requests during host maintenance', async () => {
  const royal = { ...oldAccount, username: 'royal', role: 'royal' as const }
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string, init: RequestInit) => {
    requests.push(input)
    expect(init.method ?? 'GET').toBe('GET')
    expect(init.credentials).toBe('include')
    if (input.endsWith('/auth/me')) return Response.json(royal)
    if (input.endsWith('/ops/maintenance')) return Response.json({ active: true, kind: 'update', operator: true })
    throw new Error(`Unexpected request ${input}`)
  }))
  try {
    const first = render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('royal')).toBeInTheDocument()
    first.unmount()
    const reopened = render(<AuthProvider><AuthProbe /></AuthProvider>)
    expect(await screen.findByText('royal')).toBeInTheDocument()
    expect(requests.filter(path => path.endsWith('/auth/me'))).toHaveLength(2)
    reopened.unmount()
  } finally {
    vi.unstubAllGlobals()
  }
})
