import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, api, type Park, type Report, type User } from '../api'
import { AuthContext } from '../auth-context'
import { reportsAccessIdentity, type ReportsApiClient } from '../domains/reports/reports'
import { resourceStore } from '../lib/resource'
import { ParkContext } from '../park-context'
import { Reports } from './Reports'
import { PresentationModeContext } from '../app/interface/presentationModeContext'
import { IDBFactory } from 'fake-indexeddb'
import { clearReportPhotoDrafts, readReportPhotoDraft, writeReportPhotoDraft } from '../domains/reports/reportPhotoDrafts'

const north: Park = { id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'north', tracker_queue: 'RP', is_active: true }
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

function tree(user: User, apiClient: ReportsApiClient, url = '/reports', mode: 'classic' = 'classic') {
  return (
    <PresentationModeContext.Provider value={mode}><MemoryRouter initialEntries={[url]}>
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
    </MemoryRouter></PresentationModeContext.Provider>
  )
}

function useViewport(matches: boolean) {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches,
    media: '(max-width: 599px)',
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

beforeEach(() => useViewport(false))

afterEach(() => {
  resourceStore.clearAll()
  localStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('keeps mobile report filters and summaries visible while deferring detail history', async () => {
  useViewport(true)
  const item = { ...report(9, 'Нужны подробности'), attachments: [{ id: 4, kind: 'device_photo', filename: 'photo.jpg', content_type: 'image/jpeg', size_bytes: 10 }] }
  render(tree(userA, client({ reportsMine: vi.fn(async () => [item]), report: vi.fn(async () => item) })))

  const summary = await screen.findByRole('button', { name: /Нужны подробности/ })
  expect(summary).toBeVisible()
  expect(summary).toHaveTextContent('Север')
  expect(screen.getByRole('combobox', { name: 'Статус репортов' })).toBeVisible()
  expect(screen.queryByText('photo.jpg')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /Нужны подробности/ }))
  expect(await screen.findByRole('heading', { name: 'Нужны подробности' })).toBeVisible()
  expect(screen.queryByText('photo.jpg')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'История и вложения' }))
  expect(screen.getByText('photo.jpg')).toBeVisible()
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
  expect(resourceStore.get(`${stalePrefix}mine:all:1:latest:latest`)).toEqual([report(3, 'Свежий A')])
})

it('offers a collapse control for the report list without changing its initial visibility', async () => {
  render(tree(userA, client({ reportsMine: vi.fn(async () => []) })))

  expect(await screen.findByRole('button', { name: 'Свернуть: Мои репорты' })).toBeVisible()
  expect(await screen.findByText('Вы ещё не создавали репортов.', undefined, { timeout: 3_000 })).toBeVisible()
})

it('clears a draft synchronously when effective access changes at the same principal and park', async () => {
  localStorage.setItem('robopark:report-draft:1:7', JSON.stringify({
    activeForm: 'problem', trackerKey: '', title: 'Старый секретный контекст', body: '', ownerKey: reportsAccessIdentity(userA, north),
  }))
  const apiClient = client({ reportsMine: vi.fn(async () => []) })
  const view = render(tree(userA, apiClient, '/reports/new'))
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Старый секретный контекст'))

  view.rerender(tree({ ...userA, permissions: ['nav.reports', 'reports.create', 'reports.resolve'] }, apiClient, '/reports/new'))
  expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')
  expect(localStorage.getItem('robopark:report-draft:1:7')).toBeNull()
})

it('quarantines a photo draft across access changes and restores it only to its owner', async () => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  const key = 'robopark:report-draft:1:7'
  const ownerKey = reportsAccessIdentity(userA, north)
  await writeReportPhotoDraft({ key, ownerKey, revision: 'first', activeForm: 'problem', trackerKey: '', title: 'Photo pending', body: '', createdReportId: 42, attachmentKind: 'device_photo', attachment: { blob: new Blob(['photo'], { type: 'image/jpeg' }), name: 'private.jpg', lastModified: 1 } })
  const apiClient = client({ reportsMine: vi.fn(async () => []) })
  const view = render(tree(userA, apiClient, '/reports/new'))
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Photo pending'))

  const changed = { ...userA, permissions: [...(userA.permissions ?? []), 'reports.resolve'] }
  view.rerender(tree(changed, apiClient, '/reports/new'))
  await waitFor(async () => expect(await readReportPhotoDraft(key)).toBeNull())
  expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('')

  view.rerender(tree(userA, apiClient, '/reports/new'))
  await waitFor(async () => expect((await readReportPhotoDraft(key))?.attachment?.name).toBe('private.jpg'))
  await waitFor(() => expect(screen.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Photo pending'))
  await clearReportPhotoDrafts()
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

it('hides a cached report detail after the server revokes read access', async () => {
  const item = report(9, 'Закрытая информация')
  const reportRequest = vi.fn().mockResolvedValueOnce(item)
    .mockRejectedValueOnce(new ApiError(403, 'forbidden'))
  render(tree(userA, client({ reportsMine: vi.fn(async () => [item]), report: reportRequest }), '/reports/9?park=7'))
  expect(await screen.findByRole('heading', { name: 'Закрытая информация' })).toBeVisible()

  const detailKey = `reports:${userA.id}:${reportsAccessIdentity(userA, north)}:detail:9`
  act(() => resourceStore.revalidate(detailKey))

  expect(await within(screen.getByRole('region', { name: 'Детали' })).findByRole('alert')).toHaveTextContent('Недостаточно прав.')
  expect(screen.queryByRole('heading', { name: 'Закрытая информация' })).not.toBeInTheDocument()
})

it('omits inactive page controls when a report list fits on one page', async () => {
  render(tree(userA, client({ reportsMine: vi.fn(async () => [report(3, 'Один репорт')]) })))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Один репорт' })).toBeVisible()
  expect(screen.queryByRole('navigation', { name: 'Страницы репортов' })).not.toBeInTheDocument()
})

it('pages report history and applies the mine status filter on the server', async () => {
  const history = Array.from({ length: 30 }, (_, index) => report(30 - index, `История ${index + 1}`))
  const reportsMine = vi.fn(async ({ beforeId, afterId, limit = 26, status = 'all' }: { beforeId?: number; afterId?: number; limit?: number; status?: string } = {}) => {
    const filtered = status === 'returned' ? history.filter(item => item.status === 'returned') : history
    if (afterId) return filtered.filter(item => item.id > afterId).slice(-limit)
    return filtered.filter(item => beforeId == null || item.id < beforeId).slice(0, limit)
  })
  render(tree(userA, client({ reportsMine })))

  expect(await screen.findByRole('button', { name: 'Открыть репорт История 1' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Открыть репорт История 26' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
  expect(await screen.findByRole('button', { name: 'Открыть репорт История 26' })).toBeVisible()
  expect(screen.getByLabelText('URL')).toHaveTextContent('page=2')
  expect(reportsMine).toHaveBeenCalledWith({ limit: 26, beforeId: 6, status: 'all', anchorId: 30 })

  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Статус репортов' }), 'returned')
  expect(screen.getByLabelText('URL')).not.toHaveTextContent('page=2')
  await waitFor(() => expect(reportsMine).toHaveBeenCalledWith({ limit: 26, status: 'returned' }))
})

it('keeps the report page boundary when a new report arrives before navigation', async () => {
  let history = Array.from({ length: 30 }, (_, index) => report(30 - index, `История ${index + 1}`))
  const reportsMine = vi.fn(async ({ beforeId, afterId, limit = 26, anchorId }: { beforeId?: number; afterId?: number; limit?: number; anchorId?: number } = {}) => {
    const filtered = history.filter(item => anchorId == null || item.id <= anchorId)
    if (afterId) return filtered.filter(item => item.id > afterId).slice(-limit)
    return filtered.filter(item => beforeId == null || item.id < beforeId).slice(0, limit)
  })
  render(tree(userA, client({ reportsMine })))
  expect(await screen.findByRole('button', { name: 'Открыть репорт История 1' })).toBeVisible()

  history = [report(31, 'Новый репорт'), ...history]
  await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))

  expect(await screen.findByRole('button', { name: 'Открыть репорт История 26' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Открыть репорт История 25' })).not.toBeInTheDocument()
  expect(reportsMine).toHaveBeenCalledWith({ limit: 26, beforeId: 6, status: 'all', anchorId: 30 })
  expect(screen.getByLabelText('URL')).toHaveTextContent('anchor=30')

  await userEvent.click(screen.getByRole('button', { name: 'Показать новые' }))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Новый репорт' })).toBeVisible()
  expect(screen.getByLabelText('URL')).not.toHaveTextContent('anchor=')
})

it('keeps the next inbox report when a newer report leaves the queue', async () => {
  let history = Array.from({ length: 30 }, (_, index) => report(30 - index, `Входящий ${index + 1}`, 2))
  const reportsInbox = vi.fn(async (_parkId?: number, { beforeId, limit = 26, anchorId }: { beforeId?: number; limit?: number; anchorId?: number } = {}) =>
    history.filter(item => (anchorId == null || item.id <= anchorId) && (beforeId == null || item.id < beforeId)).slice(0, limit))
  const operator = { ...userA, permissions: ['nav.reports', 'reports.resolve'] }
  render(tree(operator, client({ reportsInbox }), '/reports?pane=inbox'))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 1' })).toBeVisible()

  history = history.filter(item => item.id !== 30)
  await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))

  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 26' })).toBeVisible()
})

it('opens the first report page when a deep link lacks its history boundary', async () => {
  const reportsMine = vi.fn(async () => [report(3, 'Первая страница')])
  render(tree(userA, client({ reportsMine }), '/reports?page=2'))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Первая страница' })).toBeVisible()
  expect(reportsMine).toHaveBeenCalledWith({ limit: 26, status: 'all' })
  await waitFor(() => expect(screen.getByLabelText('URL')).not.toHaveTextContent('page=2'))
})

it('pages the operator inbox without losing older reports', async () => {
  const history = Array.from({ length: 28 }, (_, index) => report(28 - index, `Входящий ${index + 1}`, 2))
  const reportsInbox = vi.fn(async (_parkId?: number, { beforeId, afterId, limit = 26 }: { beforeId?: number; afterId?: number; limit?: number } = {}) => {
    if (afterId) return history.filter(item => item.id > afterId).slice(-limit)
    return history.filter(item => beforeId == null || item.id < beforeId).slice(0, limit)
  })
  const operator = { ...userA, permissions: ['nav.reports', 'reports.resolve'] }
  render(tree(operator, client({ reportsInbox }), '/reports?pane=inbox'))

  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 1' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Открыть репорт Входящий 26' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 26' })).toBeVisible()
  expect(reportsInbox).toHaveBeenCalledWith(7, { limit: 26, beforeId: 4, anchorId: 28 })
  await userEvent.click(screen.getByRole('button', { name: 'Предыдущая страница' }))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 1' })).toBeVisible()
  expect(reportsInbox).toHaveBeenCalledWith(7, { limit: 25, afterId: 3, anchorId: 28 })
})

it('returns to the exact middle inbox page from a third page', async () => {
  const history = Array.from({ length: 55 }, (_, index) => report(55 - index, `Входящий ${index + 1}`, 2))
  const reportsInbox = vi.fn(async (_parkId?: number, { beforeId, afterId, limit = 26 }: { beforeId?: number; afterId?: number; limit?: number } = {}) => {
    if (afterId) return history.filter(item => item.id > afterId).slice(-limit)
    return history.filter(item => beforeId == null || item.id < beforeId).slice(0, limit)
  })
  const operator = { ...userA, permissions: ['nav.reports', 'reports.resolve'] }
  render(tree(operator, client({ reportsInbox }), '/reports?pane=inbox'))

  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 1' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 26' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 51' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Предыдущая страница' }))

  expect(await screen.findByRole('button', { name: 'Открыть репорт Входящий 26' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Открыть репорт Входящий 50' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Открыть репорт Входящий 25' })).not.toBeInTheDocument()
})

it('loads only the visible report pane on entry', async () => {
  const reportsMine = vi.fn(async () => [])
  const reportsInbox = vi.fn(async () => [])
  const operator = { ...userA, permissions: ['nav.reports', 'reports.create', 'reports.resolve'] }
  render(tree(operator, client({ reportsMine, reportsInbox }), '/reports?pane=inbox'))
  await waitFor(() => expect(reportsInbox).toHaveBeenCalledTimes(1))
  expect(reportsMine).not.toHaveBeenCalled()
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

it('shows royal the complete report history without calling it open-only', async () => {
  const completed = { ...report(12, 'Проверка завершена'), status: 'done' }
  const royal = { ...userA, role: 'royal', permissions: ['nav.reports', 'reports.resolve'] }
  render(tree(royal, client({ reportsInbox: vi.fn(async () => [completed]) }), '/reports?pane=inbox'))

  expect(await screen.findByRole('button', { name: /Проверка завершена/ })).toBeVisible()
  expect(screen.getByText('Все репорты и системные уведомления по доступным паркам.')).toBeVisible()
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

it('replaces a pre-mutation pending inbox request before showing the completed report list', async () => {
  const actor = userEvent.setup()
  const oldInbox = deferred<Report[]>()
  const current = report(9, 'Завершённый репорт')
  const inbox = vi.fn().mockReturnValueOnce(oldInbox.promise).mockResolvedValue([])
  const apiClient = client({
    reportsMine: vi.fn(async () => []),
    reportsInbox: inbox,
    report: vi.fn().mockResolvedValueOnce(current).mockResolvedValue({ ...current, status: 'done' }),
    reportDone: vi.fn(async () => ({ ...current, status: 'done' })),
  })
  const resolver = { ...userA, permissions: ['nav.reports', 'reports.create', 'reports.resolve'] }
  render(tree(resolver, apiClient, '/reports/9?park=7&pane=inbox'))
  await actor.click(await screen.findByRole('button', { name: 'Готово' }))
  await waitFor(() => expect(inbox).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(screen.getByLabelText('URL')).toHaveTextContent('/reports?park=7&pane=inbox'))
  await act(async () => oldInbox.resolve([current]))
  expect(screen.queryByRole('button', { name: /Завершённый репорт/ })).not.toBeInTheDocument()
  expect(screen.getByText('Нет открытых репортов для выбранного парка.')).toBeVisible()
})
