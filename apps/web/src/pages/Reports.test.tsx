import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

it('keeps a desktop report list beside its detail and returns to the filtered URL', async () => {
  const actor = userEvent.setup()
  const returned = { ...report(9, 'Нужны подробности'), status: 'returned', return_comment: 'Уточните время' }
  const apiClient = client({ reportsMine: vi.fn(async () => [returned]), report: vi.fn(async () => returned) })
  render(tree(userA, apiClient, '/reports/9?park=7&status=returned'))
  const list = await screen.findByRole('region', { name: 'Список' })
  expect(await within(list).findByRole('button', { name: /Нужны подробности/ })).toBeInTheDocument()
  expect(await within(screen.getByRole('region', { name: 'Детали' })).findByRole('heading', { name: 'Нужны подробности' })).toBeVisible()
  await actor.click(screen.getByRole('button', { name: 'Назад к списку' }))
  expect(screen.getByLabelText('URL')).toHaveTextContent('/reports?park=7&status=returned')
  expect(screen.getByLabelText('Статус репортов')).toHaveValue('returned')
})

it.each(['driver', 'mechanic', 'operator', 'admin', 'royal', 'custom_role'])('allows %s with reports.create to open a creation form', async (role) => {
  const actor = userEvent.setup()
  render(tree({ ...userA, role }, client({ reportsMine: vi.fn(async () => []) })))
  await actor.click(screen.getByRole('link', { name: 'Создать репорт' }))
  expect(await screen.findByRole('textbox', { name: 'Заголовок *' })).toBeVisible()
})

it('does not infer report creation from resolve or admin capabilities', async () => {
  render(tree({ ...userA, role: 'royal', permissions: ['nav.reports', 'reports.resolve', 'nav.admin'] }, client({ reportsInbox: vi.fn(async () => []) })))
  expect(screen.queryByRole('link', { name: 'Создать репорт' })).not.toBeInTheDocument()
})

it('does not refresh a retired owner list after a pending post-mutation detail load completes', async () => {
  const actor = userEvent.setup()
  const lateDetail = deferred<Report>()
  const firstReport = report(9, 'Репорт A')
  const apiClient = client({
    reportsMine: vi.fn(async () => []),
    reportsInbox: vi.fn(async () => []),
    report: vi.fn().mockResolvedValueOnce(firstReport).mockImplementationOnce(() => lateDetail.promise)
      .mockResolvedValueOnce(report(9, 'Репорт B', 2)),
    reportDone: vi.fn(async () => ({ ...firstReport, status: 'done' })),
  })
  const resolver = { ...userA, permissions: ['nav.reports', 'reports.create', 'reports.resolve'] }
  const view = render(tree(resolver, apiClient, '/reports/9?park=7&pane=inbox'))
  await actor.click(await screen.findByRole('button', { name: 'Готово' }))
  await waitFor(() => expect(apiClient.report).toHaveBeenCalledTimes(2))
  view.rerender(tree({ ...resolver, id: 2, username: 'owner-b' }, apiClient, '/reports/9?park=7&pane=inbox'))
  await screen.findByRole('heading', { name: 'Репорт B' })
  const listCalls = vi.mocked(apiClient.reportsMine).mock.calls.length
  await act(async () => lateDetail.resolve({ ...firstReport, status: 'done' }))
  expect(apiClient.reportsMine).toHaveBeenCalledTimes(listCalls)
  expect(screen.getByLabelText('URL')).toHaveTextContent('/reports/9?park=7&pane=inbox')
  expect(screen.getByRole('heading', { name: 'Репорт B' })).toBeVisible()
})

it.each(['detail', 'list'] as const)('keeps selected report B when report A completes its late %s refresh', async (stage) => {
  const actor = userEvent.setup()
  const a = report(9, 'Репорт A')
  const b = report(10, 'Репорт B')
  const lateDetail = deferred<Report>()
  const lateList = deferred<Report[]>()
  const apiClient = client({
    reportsMine: vi.fn(async () => []),
    reportsInbox: vi.fn().mockResolvedValueOnce([a, b]).mockImplementation(() => lateList.promise),
    report: vi.fn().mockResolvedValueOnce(a).mockImplementationOnce(() => lateDetail.promise).mockResolvedValue(b),
    reportDone: vi.fn(async () => ({ ...a, status: 'done' })),
  })
  const resolver = { ...userA, permissions: ['nav.reports', 'reports.create', 'reports.resolve'] }
  render(tree(resolver, apiClient, '/reports/9?park=7&pane=inbox'))
  await actor.click(await screen.findByRole('button', { name: 'Готово' }))
  await waitFor(() => expect(apiClient.report).toHaveBeenCalledTimes(2))
  if (stage === 'list') {
    await act(async () => lateDetail.resolve({ ...a, status: 'done' }))
    await waitFor(() => expect(apiClient.reportsInbox).toHaveBeenCalledTimes(2))
  }
  await actor.click(within(screen.getByRole('region', { name: 'Список' })).getByRole('button', { name: /Репорт B/ }))
  expect(await screen.findByRole('heading', { name: 'Репорт B' })).toBeVisible()
  await act(async () => { lateDetail.resolve({ ...a, status: 'done' }); lateList.resolve([b]) })
  expect(screen.getByLabelText('URL')).toHaveTextContent('/reports/10?park=7&pane=inbox')
  expect(screen.getByRole('heading', { name: 'Репорт B' })).toBeVisible()
})
