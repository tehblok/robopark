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
  client?: { operationsOverview: (parkId: number, days: number, status: string) => Promise<OperationsOverview> }
  refreshUser?: () => Promise<User>
  url?: string
}

function tree({
  user = makeUser(),
  selectedPark = park as Park | null,
  parks = user.parks,
  client = { operationsOverview: vi.fn(async () => snapshot()) },
  refreshUser = vi.fn(async () => user),
  url = '/overview?park=7',
}: TreeOptions = {}) {
  return <MemoryRouter initialEntries={[url]}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser, logout: async () => {} }}><ParkScopeContext.Provider value={{ parkId: selectedPark?.id ?? null, selectedPark, parks, loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><OverviewPage apiClient={client} /><Location /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}

beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0); installMatchMedia() })
afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('composes the role-aware operational surfaces from the current overview response', async () => {
  const user = makeUser({ role: 'operator' })
  const apiClient = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ user, client: apiClient }))
  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toBeVisible()
  expect(apiClient.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.getByRole('heading', { name: 'Статусы задач' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Поток задач: пришло / ушло' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Очередь внимания' })).toBeVisible()
  expect(screen.getByText(/Пробелы не считаются нулями/)).toBeVisible()
})

it('places the attention queue before secondary operational KPIs', async () => {
  render(tree())
  await screen.findByRole('heading', { name: 'Очередь внимания' })
  const headings = screen.getAllByRole('heading').map((node) => node.textContent)
  expect(headings.indexOf('Статусы задач')).toBeLessThan(headings.indexOf('Очередь внимания'))
  expect(headings.indexOf('Очередь внимания')).toBeLessThan(headings.indexOf('Поток задач: пришло / ушло'))
})

it('collapses secondary operational KPIs behind one phone disclosure', async () => {
  installMatchMedia(390)
  render(tree())
  await screen.findByRole('heading', { name: 'Очередь внимания' })
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
  expect(await screen.findByRole('link', { name: 'Открыть задачу RP-1' })).toBeVisible()
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=7')
})

it('retains cached overview content and offers retry after deferred offline revalidation', async () => {
  const revalidation = deferred<OperationsOverview>()
  const client = { operationsOverview: vi.fn().mockResolvedValueOnce(snapshot()).mockImplementationOnce(() => revalidation.promise).mockResolvedValue(snapshot({ tasks: [] })) }
  render(tree({ client }))

  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
  fireEvent.focus(window)
  await waitFor(() => expect(client.operationsOverview).toHaveBeenCalledTimes(2))
  await act(async () => revalidation.reject(new TypeError('offline')))
  expect(await screen.findByText('Нет сети')).toBeVisible()
  expect(screen.getByRole('link', { name: 'Открыть задачу RP-1' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await screen.findByText('Нет задач в очереди внимания')
})

it.each([401, 403])('clears cached protected overview data and refreshes auth after %s revalidation', async (status) => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn().mockResolvedValueOnce(snapshot()).mockRejectedValue(new ApiError(status, null, 'denied')) }
  render(tree({ user, refreshUser, client }))

  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
  fireEvent.focus(window)
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
