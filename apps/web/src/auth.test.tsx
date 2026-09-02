import { act, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, ApiTimeoutError, api, type User } from './api'
import { AuthProvider } from './auth'
import { type AuthContextValue, useAuth } from './auth-context'
import { resourceStore } from './lib/resource'

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
  expect(localStorage.getItem(reportKey)).toBeNull()
  expect(localStorage.getItem(otherReportKey)).toBeNull()
  expect(localStorage.getItem(legacyRecentKey)).toBe('["447"]')
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
  afterEach(() => {
    currentAuth = null
    resourceStore.clearAll()
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('clears protected state when the initial session request returns 401', async () => {
    vi.spyOn(api, 'me').mockRejectedValueOnce(new ApiError(401))
    seedProtectedState()

    render(<AuthProvider><AuthProbe /></AuthProvider>)

    expect(await screen.findByText('anonymous')).toBeInTheDocument()
    expectProtectedStateCleared()
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
      expect(localStorage.getItem(reportKey)).toBeNull()
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
    expect(localStorage.getItem(reportKey)).toBeNull()
  })

  it.each([
    ['offline', new TypeError('Failed to fetch')],
    ['timeout', new ApiTimeoutError(30_000)],
    ['server', new ApiError(503, 'tracker_upstream_error')],
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
