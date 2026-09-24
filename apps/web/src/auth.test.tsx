import { act, render, screen } from '@testing-library/react'
import { StrictMode, useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, ApiTimeoutError, api, type User } from './api'
import { AuthProvider } from './auth'
import { type AuthContextValue, useAuth } from './auth-context'
import { resourceStore } from './lib/resource'
import { IDBFactory } from 'fake-indexeddb'
import { openOfflineDb, purgeOfflineScope } from './pwa/offlineDb'
import { offlineScopeForUser } from './lib/deviceResourceCache'
import { openShareTargetInbox } from './pwa/ShareTargetInbox'

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

let currentAuth: AuthContextValue | null = null

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function AuthProbe() {
  const auth = useAuth()
  useEffect(() => {
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
}

describe('AuthProvider session boundaries', () => {
  afterEach(async () => {
    await purgeOfflineScope()
    currentAuth = null
    resourceStore.clearAll()
    localStorage.clear()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
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

  it('purges unclaimed global share-target photos when refresh replaces the account', async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    vi.spyOn(api, 'me').mockResolvedValueOnce(oldAccount).mockResolvedValueOnce(replacementAccount)
    render(<AuthProvider><AuthProbe /></AuthProvider>)
    await screen.findByText('old-account')
    const inbox = await openShareTargetInbox()
    await inbox.save({ id: 'unclaimed', createdAt: Date.now(), name: 'private.jpg', type: 'image/jpeg', blob: new Blob(['private']), assignment: null })
    await act(async () => { await currentAuth!.refreshUser() })
    expect(await inbox.list()).toEqual([])
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

    await act(async () => { await currentAuth!.logout() })
    expect(localStorage.getItem('robopark:report-draft:3:all')).toBeNull()
    expect(await inbox.list()).toEqual([])
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
