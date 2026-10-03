import { beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type OperationsOverview, type Park, type User } from '../../api'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { deferred, makeUser, park, snapshot } from '../insights/operations.test-support'
import { OverviewPage } from './OverviewPage'

function installMatchMedia(width = 1200) {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches: width < 600, media: '(max-width: 599px)', onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  }))
}

function Location() { return <output aria-label="URL">{useLocation().search}</output> }

type TreeOptions = {
  user?: User
  selectedPark?: Park | null
  parks?: Park[]
  loadError?: string | null
  refreshParks?: () => Promise<void>
  client?: { operationsOverview: (parkId: number, days: number, status: string) => Promise<OperationsOverview> }
  refreshUser?: () => Promise<User>
  url?: string
}

function tree({
  user = makeUser(),
  selectedPark = park as Park | null,
  parks = user.parks,
  loadError = null,
  refreshParks = async () => {},
  client = { operationsOverview: vi.fn(async () => snapshot()) },
  refreshUser = vi.fn(async () => user),
  url = '/overview?park=7',
}: TreeOptions = {}) {
  return <MemoryRouter initialEntries={[url]}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser, logout: async () => {} }}><ParkScopeContext.Provider value={{ parkId: selectedPark?.id ?? null, selectedPark, parks, loading: false, loadError, locked: false, setParkId: vi.fn(), refreshParks }}><OverviewPage apiClient={client} /><Location /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}

beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0); installMatchMedia() })
afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('guides the first owner to create a park instead of leaving an unexplained empty overview', () => {
  const user = makeUser({ role: 'royal', parks: [], permissions: ['nav.dashboard', 'parks.manage', 'tracker.read'] })
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ user, selectedPark: null, parks: [], client, url: '/overview' }))

  expect(screen.getByRole('heading', { name: 'Парков пока нет' })).toBeVisible()
  expect(screen.getByRole('link', { name: 'Создать первый парк' })).toHaveAttribute('href', '/admin/settings?tab=parks')
  expect(client.operationsOverview).not.toHaveBeenCalled()
})

it('guides an owner with integration access from a missing Tracker token to its settings', async () => {
  const user = makeUser({ role: 'royal', permissions: ['nav.dashboard', 'nav.admin', 'tracker.read'] })
  const client = { operationsOverview: vi.fn(async () => { throw new ApiError(503, 'tracker_token_not_configured') }) }
  render(tree({ user, client }))

  expect(await screen.findByRole('heading', { name: 'Требуется настройка' })).toBeVisible()
  expect(screen.getByRole('link', { name: 'Настроить Tracker' })).toHaveAttribute('href', '/admin/settings?park=7&tab=integrations#tracker-token')
})

it('guides the owner to configure a park without a Tracker queue', async () => {
  const user = makeUser({ role: 'royal', permissions: ['nav.dashboard', 'nav.admin', 'parks.manage', 'tracker.read'] })
  const client = { operationsOverview: vi.fn(async () => { throw new ApiError(409, 'blockers_disabled_for_park') }) }
  render(tree({ user, client }))

  expect(await screen.findByRole('heading', { name: 'Требуется настройка' })).toBeVisible()
  expect(screen.getByRole('link', { name: 'Настроить парк' })).toHaveAttribute('href', '/admin/settings?park=7&tab=parks')
  expect(screen.queryByText('Данные изменились')).not.toBeInTheDocument()
})

it('does not offer integration settings to a mechanic without management access', async () => {
  const user = makeUser({ role: 'mechanic', permissions: ['nav.dashboard', 'tracker.read'] })
  const client = { operationsOverview: vi.fn(async () => { throw new ApiError(503, 'tracker_token_not_configured') }) }
  render(tree({ user, client }))

  expect(await screen.findByRole('heading', { name: 'Требуется настройка' })).toBeVisible()
  expect(screen.queryByRole('link', { name: 'Настроить Tracker' })).not.toBeInTheDocument()
})

it('shows park-load failure and a retry instead of reporting that no park was selected', async () => {
  const user = makeUser({ role: 'royal', parks: [], permissions: ['nav.dashboard', 'parks.manage', 'tracker.read'] })
  const refreshParks = vi.fn(async () => {})
  render(tree({ user, selectedPark: null, parks: [], loadError: 'Не удалось загрузить парки.', refreshParks, url: '/overview' }))

  expect(screen.getByRole('alert')).toHaveTextContent('Не удалось загрузить парки.')
  expect(screen.queryByRole('heading', { name: 'Парков пока нет' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await waitFor(() => expect(refreshParks).toHaveBeenCalledTimes(1))
})

it('keeps the last authorized park overview visible when refreshing the park list fails', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ loadError: 'Не удалось обновить парки.', client }))

  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toBeVisible()
  expect(screen.getByText('Не удалось обновить парки.').closest('[role="alert"]')).toBeTruthy()
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
})

it('composes the role-aware operational surfaces from the current overview response', async () => {
  const user = makeUser({ role: 'operator' })
  const apiClient = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ user, client: apiClient }))
  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toBeVisible()
  expect(apiClient.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.getByRole('heading', { name: 'Статусы задач' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Поток задач: пришло / ушло' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Очередь решений' })).toBeVisible()
  expect(screen.getByText(/Пробелы не считаются нулями/)).toBeVisible()
})

it('shows the shift dashboard before the detailed queue with no extra request', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ client }))
  const dashboard = await screen.findByRole('region', { name: 'Сводка смены' })
  expect(within(dashboard).getByText('Активные задачи')).toBeVisible()
  expect(within(dashboard).getByText('Просрочено SLA')).toBeVisible()
  expect(within(dashboard).getByText('Риск SLA')).toBeVisible()
  expect(within(dashboard).getByText('SLA не определён')).toBeVisible()
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
  expect(dashboard.compareDocumentPosition(screen.getByRole('heading', { name: 'Очередь решений' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('uses the selected A layout with immediate priorities and a side attention area from one snapshot', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ client }))

  expect(screen.getByRole('heading', { name: 'Что требует решения сейчас' })).toBeVisible()
  const summary = await screen.findByRole('region', { name: 'Сводка смены' })
  expect(summary).toHaveClass('rp-overview-headline')
  expect(summary.closest('.rp-panel')).toBeNull()
  expect(summary.querySelectorAll('.rp-metric-card__value')).toHaveLength(4)
  expect(summary.querySelectorAll('.rp-metric-card[data-variant="prominent"]')).toHaveLength(4)
  expect(summary.querySelectorAll('.rp-metric-card__delta')).toHaveLength(0)
  expect(summary.querySelectorAll('.rp-metric-card__value[title="Не измерено"]')).toHaveLength(3)
  const primary = screen.getByTestId('overview-primary')
  expect(within(primary).getByRole('heading', { name: 'Очередь решений' })).toBeVisible()
  expect(within(primary).getByRole('heading', { name: 'Что требует внимания' })).toBeVisible()
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
})

it('prioritizes remaining SLA without a downtime column', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot({
    task_timing: [{ issue_key: 'RP-1', queue_started_at: '2026-09-01T09:00:00Z', sla_deadline: '2026-09-01T14:00:00Z', sla_working_hours: 4, downtime_hours: 12 }],
  })) }
  render(tree({ client }))
  const queue = await screen.findByTestId('overview-attention-queue')
  expect(queue).not.toHaveTextContent('Простой')
  expect(queue).toHaveTextContent('SLA: 1:00')
  expect(queue).not.toHaveTextContent('Возраст')
})

it('shows a task table with assignee, deadline in park time and accessible action', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot({
    tasks: [{ ...snapshot().tasks[0], assignee: { display: 'Механик А', login: 'mech-a' } }],
    task_timing: [{ issue_key: 'RP-1', queue_started_at: '2026-09-03T08:00:00Z', sla_deadline: '2026-09-03T13:00:00Z', sla_working_hours: 2, downtime_hours: 3 }],
  })) }
  render(tree({ client }))
  const table = await screen.findByRole('table', { name: 'Задачи смены' })
  expect(within(table).getAllByRole('columnheader')).toHaveLength(3)
  expect(within(table).getByRole('columnheader', { name: 'Этап и исполнитель' })).toBeVisible()
  const row = within(table).getByRole('row', { name: /RP-1/ })
  expect(within(row).getByText('Механик А')).toBeVisible()
  expect(within(row).getByText(/03\.09.*16:00/)).toBeVisible()
  expect(within(row).getByRole('link', { name: 'Открыть задачу RP-1' })).toHaveAttribute('href', '/work/RP-1?park=7')
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
})

it('opens the paginated work queue from a truncated status slice', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot({
    selected_status: 'diagnostics',
    tasks_truncated: true,
  })) }
  render(tree({ client, url: '/overview?park=7&status=diagnostics' }))

  const queue = await screen.findByRole('region', { name: 'Очередь решений' })
  expect(within(queue).getByRole('link', { name: 'Вся очередь в «Работе»' }))
    .toHaveAttribute('href', '/work?park=7&status=diagnostics')
  expect(queue).toHaveTextContent('Список задач ограничен данными текущего ответа.')
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
})

it('keeps the Tracker stage visible when a task is overdue', async () => {
  const issue = { ...snapshot().tasks[0], status: 'В работе', bucket: 'diagnostics' }
  const client = { operationsOverview: vi.fn(async () => snapshot({
    tasks: [issue],
    sla: {
      target_hours: 5,
      evaluated_count: 1,
      unknown_count: 0,
      at_risk_count: 0,
      overdue_count: 1,
      overdue: [{ ...issue, age_hours: 6, overdue_hours: 1 }],
      overdue_truncated: false,
    },
  })) }
  render(tree({ client }))

  const row = await screen.findByRole('row', { name: /RP-1/ })
  expect(within(row).getByText('В работе')).toBeVisible()
  expect(row).toHaveTextContent('Просрочено SLA')
})

it('shows a passed closing-time deadline without zero-hour overdue text', async () => {
  const issue = snapshot().tasks[0]
  const client = { operationsOverview: vi.fn(async () => snapshot({
    tasks: [issue],
    sla: {
      target_hours: 5,
      evaluated_count: 1,
      unknown_count: 0,
      at_risk_count: 0,
      overdue_count: 1,
      overdue: [{ ...issue, age_hours: 5, overdue_hours: 0 }],
      overdue_truncated: false,
    },
  })) }
  render(tree({ client }))

  const row = await screen.findByRole('row', { name: /RP-1/ })
  expect(row).toHaveTextContent('Срок SLA истёк')
  expect(row).not.toHaveTextContent('Просрочено на 0.0 ч')
})

it('shows confirmed SLA risk without replacing the Tracker stage', async () => {
  render(tree({ client: { operationsOverview: vi.fn(async () => snapshot({
    sla: { ...snapshot().sla, target_hours: 5 },
    task_timing: [{ issue_key: 'RP-1', queue_started_at: '2026-09-03T08:00:00Z', sla_deadline: '2026-09-03T13:00:00Z', sla_working_hours: 4, downtime_hours: 4 }],
  })) } }))

  const row = await screen.findByRole('row', { name: /RP-1/ })
  expect(row).toHaveTextContent('Риск SLA')
  expect(row).toHaveTextContent('Новый')
})

it('keeps absent assignment and queue timing visibly unknown', async () => {
  render(tree())
  const row = await screen.findByRole('row', { name: /RP-1/ })
  expect(within(row).getByText('Не назначен')).toBeVisible()
  expect(within(row).getByText('Срок: Неизвестен')).toBeVisible()
  expect(row).toHaveTextContent('SLA: —')
  expect(row).not.toHaveTextContent('Простой')
})

it('shows the attention queue before the status breakdown and secondary KPIs', async () => {
  render(tree())
  await screen.findByRole('heading', { name: 'Очередь решений' })
  const headings = screen.getAllByRole('heading').map((node) => node.textContent)
  expect(headings.indexOf('Очередь решений')).toBeLessThan(headings.indexOf('Статусы задач'))
  expect(headings.indexOf('Очередь решений')).toBeLessThan(headings.indexOf('Поток задач: пришло / ушло'))
})

it('collapses secondary operational KPIs behind one phone disclosure', async () => {
  installMatchMedia(390)
  render(tree())
  await screen.findByRole('heading', { name: 'Очередь решений' })
  expect(screen.getByRole('button', { name: 'Дополнительные показатели' })).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('heading', { name: 'Поток задач: пришло / ушло' })).not.toBeInTheDocument()
})

it('shows unknown flow counts instead of treating no observed intervals as zero', async () => {
  const flow = { ...snapshot().flow, observed_buckets: 0, complete: false, points: [] }
  const client = { operationsOverview: vi.fn(async () => snapshot({ counts: { ...snapshot().counts, all: 1 }, flow })) }
  render(tree({ client }))

  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  expect(within(screen.getByLabelText('Сводка потока задач')).getAllByText('Нет данных')).toHaveLength(2)
})

it.each([
  ['driver', 'moving', ['Новые: 4 задач', 'Перемещение: 2 задач']],
  ['mechanic', 'diagnostics', ['Очередь: 1 задач', 'Диагностика: 1 задач']],
] as const)('keeps %s status monitoring-only and normalizes direct allowed status URLs', async (role, status, labels) => {
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ user: makeUser({ role }), client, url: `/overview?park=7&status=${status}` }))

  await screen.findByRole('heading', { name: 'Статусы задач' })
  expect(client.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=7')
  const monitoring = screen.getByTestId('overview-statuses')
  expect(within(monitoring).queryAllByRole('link')).toHaveLength(0)
  for (const label of labels) expect(screen.queryByRole('link', { name: label })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Все разрешённые задачи' })).not.toBeInTheDocument()
})

it('keeps privileged status selection and clears a selected status back to all permitted tasks', async () => {
  const client = { operationsOverview: vi.fn(async (_park: number, _days: number, status: string) => snapshot({ selected_status: status })) }
  render(tree({ client }))

  fireEvent.click(await screen.findByRole('link', { name: 'Перемещение: 2 задач' }))
  await waitFor(() => expect(client.operationsOverview).toHaveBeenLastCalledWith(7, 7, 'moving'))
  fireEvent.click(await screen.findByRole('link', { name: 'Все разрешённые задачи' }))
  await waitFor(() => expect(client.operationsOverview).toHaveBeenLastCalledWith(7, 7, 'all'))
  expect(client.operationsOverview).toHaveBeenCalledTimes(3)
  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toBeVisible()
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=7')
})

it('revalidates on reopening while retaining cached overview content after a network failure', async () => {
  const revalidation = deferred<OperationsOverview>()
  const client = { operationsOverview: vi.fn().mockResolvedValueOnce(snapshot()).mockImplementationOnce(() => revalidation.promise).mockResolvedValue(snapshot({ tasks: [] })) }
  const view = render(tree({ client }))

  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  view.unmount()
  render(tree({ client }))
  await waitFor(() => expect(client.operationsOverview).toHaveBeenCalledTimes(2))
  await act(async () => revalidation.reject(new TypeError('offline')))
  await waitFor(() => expect(document.querySelector('.rp-overview-warning')).toHaveTextContent('Нет сети'))
  expect(screen.getByRole('link', { name: 'Открыть задачу RP-1' })).toBeVisible()
  fireEvent.click(within(document.querySelector('.rp-overview-warning') as HTMLElement).getByRole('button', { name: 'Повторить' }))
  await screen.findByText('Нет задач в очереди внимания')
})

it('loads on opening without polling Tracker while the overview remains open', async () => {
  vi.useFakeTimers()
  try {
    const client = { operationsOverview: vi.fn(async () => snapshot()) }
    render(tree({ client }))
    await act(async () => {})
    expect(client.operationsOverview).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(120_000))
    expect(client.operationsOverview).toHaveBeenCalledTimes(1)
  } finally {
    vi.useRealTimers()
  }
})

it.each([401, 403])('clears cached protected overview data and refreshes auth after %s on reopening', async (status) => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn().mockResolvedValueOnce(snapshot()).mockRejectedValue(new ApiError(status, null, 'denied')) }
  const view = render(tree({ user, refreshUser, client }))

  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  view.unmount()
  render(tree({ user, refreshUser, client }))
  expect(await screen.findByRole('heading', { name: status === 403 ? 'Нет доступа' : 'Сессия истекла' })).toBeVisible()
  expect(screen.queryByRole('link', { name: 'Открыть задачу RP-1' })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it.each(['park', 'access'] as const)('releases a 403 after the current %s identity changes', async change => {
  const user = makeUser()
  const otherPark = { ...park, id: 8, name: 'Юг', tag: 'Beta' }
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn().mockRejectedValueOnce(new ApiError(403, null, 'park-a')).mockResolvedValue(snapshot({ park_id: change === 'park' ? 8 : 7 })) }
  const view = render(tree({ user, client, refreshUser }))
  await screen.findByRole('heading', { name: 'Нет доступа' })
  view.rerender(tree({ user: change === 'access' ? { ...user, permissions: [...user.permissions!, 'reports.create'] } : user, selectedPark: change === 'park' ? otherPark : park, client, refreshUser }))
  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toHaveAttribute('href', `/work/RP-1?park=${change === 'park' ? 8 : 7}`)
  expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
})

it.each([200, 403])('ignores late park A completion %s after park B has loaded', async status => {
  const old = deferred<OperationsOverview>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn().mockReturnValueOnce(old.promise).mockResolvedValue(snapshot({ park_id: 8 })) }
  const view = render(tree({ user, client, refreshUser }))
  await waitFor(() => expect(client.operationsOverview).toHaveBeenCalledTimes(1))
  view.rerender(tree({ user, selectedPark: { ...park, id: 8, name: 'Юг', tag: 'Beta' }, client, refreshUser }))
  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toHaveAttribute('href', '/work/RP-1?park=8')
  await act(async () => { if (status === 403) old.reject(new ApiError(403, null, 'old-park')); else old.resolve(snapshot({ tasks: [] })) })
  expect(screen.getByRole('link', { name: 'Открыть задачу RP-1' })).toHaveAttribute('href', '/work/RP-1?park=8')
  expect(refreshUser).not.toHaveBeenCalled()
})

it('keeps a 401 session denial after park changes without retrying protected requests', async () => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn().mockRejectedValue(new ApiError(401, null, 'session')) }
  const view = render(tree({ user, client, refreshUser }))
  await screen.findByRole('heading', { name: 'Сессия истекла' })
  view.rerender(tree({ user, selectedPark: { ...park, id: 8 }, client, refreshUser }))
  expect(screen.getByRole('heading', { name: 'Сессия истекла' })).toBeVisible()
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it.each(['admin', 'royal', 'operator'])('shows every accessible park separately for %s', async role => {
  const user = makeUser({ role })
  const client = { operationsOverview: vi.fn(async (parkId: number) => snapshot({ park_id: parkId })) }
  render(tree({ user, selectedPark: null, client, url: '/overview' }))
  for (const entry of user.parks) {
    const section = await screen.findByRole('region', { name: entry.name })
    expect(await within(section).findByRole('link', { name: 'Открыть задачу RP-1' })).toHaveAttribute('href', `/work/RP-1?park=${entry.id}`)
    expect(within(section).getByRole('link', { name: 'Перемещение: 2 задач' })).toHaveAttribute('href', `/overview?status=moving&park=${entry.id}`)
    expect(within(section).getByRole('heading', { name: 'Поток задач: пришло / ушло' })).toBeVisible()
  }
  expect(client.operationsOverview).toHaveBeenCalledTimes(2)
})

it('limits operator overview to assigned active parks even with fleet management permission', async () => {
  const inactive = { ...park, id: 10, name: 'Закрыт', is_active: false }
  const user = makeUser({ parks: [park, inactive], permissions: [...makeUser().permissions!, 'parks.manage'] })
  const client = { operationsOverview: vi.fn(async (parkId: number) => snapshot({ park_id: parkId })) }
  render(tree({ user, parks: [...makeUser().parks, inactive], selectedPark: null, client }))
  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
  expect(client.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.queryByRole('region', { name: 'Юг' })).not.toBeInTheDocument()
})

it.each([401, 403])('stops queued parks and hides every park when one overview returns %s', async status => {
  const parks = Array.from({ length: 6 }, (_, index) => ({ ...park, id: index + 1, name: `Парк ${index + 1}` }))
  const pending = parks.map(() => deferred<OperationsOverview>())
  const user = makeUser({ role: 'admin', parks })
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn((id: number) => pending[id - 1].promise) }
  render(tree({ user, selectedPark: null, client, refreshUser }))
  await waitFor(() => expect(client.operationsOverview).toHaveBeenCalledTimes(3))
  await act(async () => pending[0].reject(new ApiError(status, null, 'denied')))
  await screen.findByRole('heading', { name: status === 401 ? 'Сессия истекла' : 'Нет доступа' })
  await act(async () => { pending[1].resolve(snapshot({ park_id: 2 })); pending[2].resolve(snapshot({ park_id: 3 })) })
  expect(client.operationsOverview).toHaveBeenCalledTimes(3)
  expect(screen.queryByRole('link', { name: 'Открыть задачу RP-1' })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it.each([200, 403])('retires queued parks and ignores late all-parks completion %s after selecting a park', async status => {
  const parks = Array.from({ length: 6 }, (_, index) => ({ ...park, id: index + 1, name: `Парк ${index + 1}` }))
  const pending = parks.map(() => deferred<OperationsOverview>())
  const user = makeUser({ role: 'admin', parks })
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn((id: number) => pending[id - 1].promise) }
  const view = render(tree({ user, selectedPark: null, client, refreshUser }))
  await waitFor(() => expect(client.operationsOverview).toHaveBeenCalledTimes(3))
  view.rerender(tree({ user, selectedPark: parks[5], client, refreshUser }))
  await act(async () => { if (status === 403) pending[0].reject(new ApiError(403, null, 'retired')); else pending[0].resolve(snapshot({ park_id: 1 })) })
  await waitFor(() => expect(client.operationsOverview).toHaveBeenCalledTimes(4))
  expect(client.operationsOverview.mock.calls.map(([id]) => id)).toEqual([1, 2, 3, 6])
  await act(async () => { pending[5].resolve(snapshot({ park_id: 6 })); pending[1].resolve(snapshot({ park_id: 2 })); pending[2].resolve(snapshot({ park_id: 3 })) })
  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toHaveAttribute('href', '/work/RP-1?park=6')
  expect(refreshUser).not.toHaveBeenCalled()
})
