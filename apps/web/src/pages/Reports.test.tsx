import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api, type Park, type Report, type User } from '../api'
import { AuthContext } from '../auth-context'
import { reportsAccessIdentity, type ReportsApiClient } from '../domains/reports/reports'
import { resourceStore } from '../lib/resource'
import { ParkContext } from '../park-context'
import { Reports } from './Reports'

const north: Park = { id: 7, name: 'Север', tag: 'north', tracker_queue: 'RP', is_active: true }
const userA: User = {
  id: 1,
  username: 'owner-a',
  role: 'operator',
  access_status: 'approved',
  permissions: ['nav.reports', 'reports.create'],
  parks: [north],
}

function report(id: number, title: string, author = 1): Report {
  return {
    id,
    kind: 'mechanic_problem',
    status: 'open',
    park_id: 7,
    author_user_id: author,
    target_role: 'operator',
    tracker_key: null,
    tracker_url: null,
    title,
    body: '',
    parent_report_id: null,
    return_comment: null,
    created_at: '2026-09-04T08:00:00Z',
    updated_at: '2026-09-04T08:00:00Z',
    resolved_at: null,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((onResolve) => { resolve = onResolve })
  return { promise, resolve }
}

function client(overrides: Partial<ReportsApiClient>): ReportsApiClient {
  return { ...api, reportsBadge: vi.fn(async () => ({ count: 0 })), ...overrides }
}

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="URL">{location.pathname}{location.search}</output>
}

function tree(user: User, apiClient: ReportsApiClient, url = '/reports') {
  return (
    <MemoryRouter initialEntries={[url]}>
      <AuthContext.Provider value={{
        user,
        loading: false,
        login: vi.fn(async () => user),
        refreshUser: vi.fn(async () => user),
        logout: vi.fn(async () => undefined),
      }}>
        <ParkContext.Provider value={{
          parkId: 7,
          setParkId: vi.fn(),
          parks: user.parks,
          parksLoading: false,
          parkLocked: false,
        }}>
          <Routes>
            <Route element={<Reports apiClient={apiClient} />} path="/reports" />
            <Route element={<Reports apiClient={apiClient} />} path="/reports/new" />
            <Route element={<Reports apiClient={apiClient} />} path="/reports/:reportId" />
          </Routes>
          <LocationProbe />
        </ParkContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

afterEach(() => {
  resourceStore.clearAll()
  localStorage.clear()
  vi.restoreAllMocks()
})

it('releases a pending A owner before B and a remounted A start fresh', async () => {
  const oldA = deferred<Report[]>()
  const reportsMine = vi.fn()
    .mockImplementationOnce(() => oldA.promise)
    .mockResolvedValueOnce([report(2, 'Только B', 2)])
    .mockResolvedValueOnce([report(3, 'Свежий A')])
  const apiClient = client({ reportsMine })
  const userB = { ...userA, id: 2, username: 'owner-b' }
  const view = render(tree(userA, apiClient))
  await waitFor(() => expect(reportsMine).toHaveBeenCalledTimes(1))

  view.rerender(tree(userB, apiClient))
  expect(await screen.findByRole('button', { name: /Только B/ })).toBeVisible()
  view.rerender(tree(userA, apiClient))
  expect(await screen.findByRole('button', { name: /Свежий A/ })).toBeVisible()
  expect(reportsMine).toHaveBeenCalledTimes(3)

  await act(async () => oldA.resolve([report(1, 'Устаревший A')]))
  expect(screen.queryByText('Устаревший A')).not.toBeInTheDocument()
  const stalePrefix = `reports:${userA.id}:${reportsAccessIdentity(userA, north)}:`
  expect(resourceStore.get(`${stalePrefix}mine`)).toEqual([report(3, 'Свежий A')])
})

it('clears a draft synchronously when effective access changes at the same principal and park', async () => {
  localStorage.setItem('robopark:report-draft:1:7', JSON.stringify({
    activeForm: 'problem', trackerKey: '', title: 'Старый секретный контекст', body: '',
  }))
  const apiClient = client({ reportsMine: vi.fn(async () => []) })
  const view = render(tree(userA, apiClient, '/reports/new'))
  expect(await screen.findByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Старый секретный контекст')

  view.rerender(tree({ ...userA, permissions: ['nav.reports', 'reports.create', 'reports.resolve'] }, apiClient, '/reports/new'))
  expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')
  expect(localStorage.getItem('robopark:report-draft:1:7')).toBeNull()
})
