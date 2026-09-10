import { beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError } from '../../api'
import { AuthProvider } from '../../auth'
import { ParkScopeProvider } from '../../app/park/ParkScopeProvider'
import { resourceStore } from '../../lib/resource'
import { InsightsPage } from './InsightsPage'
import { deferred, makeUser, otherPark, park, snapshot, tree } from './operations.test-support'

afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks() })

it.each([401, 403])('refreshes auth once without retrying Operations when a same-principal %s survives the real park lifecycle', async (status) => {
  const user = makeUser({ role: 'admin' })
  const refreshedUser = { ...user, permissions: [...(user.permissions ?? [])], parks: [...user.parks] }
  const me = vi.spyOn(api, 'me').mockResolvedValueOnce(user).mockResolvedValue(refreshedUser)
  const parks = vi.spyOn(api, 'parks').mockResolvedValue([park, otherPark])
  const hanging = deferred<ReturnType<typeof snapshot>>()
  const client = { operationsOverview: vi.fn().mockRejectedValueOnce(new ApiError(status)).mockImplementation(() => hanging.promise) }
  const view = render(<MemoryRouter initialEntries={['/overview?park=7']}><AuthProvider><ParkScopeProvider><InsightsPage apiClient={client} mode="overview" /></ParkScopeProvider></AuthProvider></MemoryRouter>)

  await waitFor(() => expect(me).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(parks).toHaveBeenCalledTimes(2))
  expect(client.operationsOverview).toHaveBeenCalledTimes(1)
  expect(screen.getByRole('heading', { name: status === 403 ? 'Нет доступа' : 'Сессия истекла' })).toBeVisible()
  view.unmount()
  await act(async () => hanging.resolve(snapshot()))
})

it('starts a fresh authorization lifecycle for a different principal', async () => {
  const first = makeUser()
  const second = { ...first, id: 99, username: 'other-operator' }
  const refreshUser = vi.fn(async () => first)
  const client = { operationsOverview: vi.fn().mockRejectedValueOnce(new ApiError(403)).mockResolvedValue(snapshot({ tasks: [] })) }
  const view = render(tree({ user: first, refreshUser, client }))
  await screen.findByRole('heading', { name: 'Нет доступа' })

  view.rerender(tree({ user: second, refreshUser, client }))
  expect(await screen.findByText('Нет задач в выбранных статусах')).toBeVisible()
  expect(client.operationsOverview).toHaveBeenCalledTimes(2)
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it.each(['driver', 'mechanic', 'operator', 'admin', 'royal'])('%s loads tasks for the selected park from the shared endpoint', async role => {
  const buckets = role === 'driver' ? ['new', 'moving'] : ['new', 'moving', 'queued', 'diagnostics']
  const data = snapshot({ tasks: buckets.map((bucket, index) => ({ ...snapshot().tasks[0], key: `RP-${index}`, bucket, summary: `Задача ${bucket}` })), status_options: snapshot().status_options.filter(option => option.key === 'all' || buckets.includes(option.key)) })
  const client = { operationsOverview: vi.fn(async () => data) }
  render(tree({ user: makeUser({ role }), client }))
  expect(await screen.findByText(`Задача ${buckets[0]}`)).toBeVisible()
  expect(client.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.getByRole('heading', { name: 'Просрочки SLA' })).toBeVisible()
  if (role === 'driver' || role === 'mechanic') {
    expect(screen.queryByRole('heading', { name: 'Нагрузка по ответственным' })).not.toBeInTheDocument()
  }
  expect(screen.getByRole('link', { name: /Открыть задачу RP-0/ })).toHaveAttribute('href', expect.stringContaining('/work/RP-0?park=7'))
})

it('shows returned workload and operator statistics without calling them productivity', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot({
    workload: [{ login: 'operator.one', display: 'Оператор 1', open_count: 3, overdue_count: 1, oldest_hours: 18 }],
    operators: [{ user_id: 11, username: 'operator1', tracker_login: 'operator.one', open_count: 3, overdue_count: 1, oldest_hours: 18 }],
  })) }
  render(tree({ client, mode: 'analytics', user: makeUser({ role: 'admin' }), url: '/analytics?park=7' }))
  expect(await screen.findByRole('heading', { name: 'Нагрузка по ответственным' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Учётные записи операторов' })).toBeVisible()
  expect(screen.getAllByText('Открыто: 3')).toHaveLength(2)
  expect(screen.queryByText(/productivity|производительност/i)).not.toBeInTheDocument()
})

it('shares period/status in the URL without changing full snapshot counts', async () => {
  const client = { operationsOverview: vi.fn(async (_park: number, _days: number, status: string) => snapshot({ selected_status: status, tasks: status === 'moving' ? [] : snapshot().tasks })) }
  render(tree({ client }))
  await screen.findByText('Проверить колесо')
  fireEvent.change(screen.getByLabelText('Период'), { target: { value: '30' } })
  await waitFor(() => expect(client.operationsOverview).toHaveBeenLastCalledWith(7, 30, 'all'))
  fireEvent.change(screen.getByLabelText('Статус задач'), { target: { value: 'moving' } })
  await screen.findByText('Нет задач в выбранных статусах')
  expect(client.operationsOverview).toHaveBeenLastCalledWith(7, 30, 'moving')
  expect(screen.getByLabelText('URL')).toHaveTextContent('park=7&days=30&status=moving')
  expect(within(screen.getByLabelText('Текущие показатели')).getByText('4')).toBeVisible()
})

it.each(['operator', 'admin', 'royal'])('%s sends a changed status with the selected park', async (role) => {
  const client = { operationsOverview: vi.fn(async (_park: number, _days: number, status: string) => snapshot({ park_id: 8, selected_status: status })) }
  render(tree({ client, selectedPark: otherPark, user: makeUser({ role }), url: '/overview?park=8' }))
  await screen.findByText('Проверить колесо')

  fireEvent.change(screen.getByLabelText('Статус задач'), { target: { value: 'waiting_team' } })
  await waitFor(() => expect(client.operationsOverview).toHaveBeenLastCalledWith(8, 7, 'waiting_team'))
  expect(screen.getByLabelText('URL')).toHaveTextContent('park=8&status=waiting_team')
})

it('normalizes invalid period and forbidden role status without broadening scope', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ client, user: makeUser({ role: 'driver' }), url: '/overview?park=7&days=999&status=diagnostics' }))
  await screen.findByText('Проверить колесо')
  expect(client.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=7')
})

it.each(['tracker.read', 'nav.dashboard'])('fails closed before reads when %s is denied', async permission => {
  const user = makeUser({ permissions: ['nav.dashboard', 'tracker.read'].filter(p => p !== permission) })
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ user, client }))
  expect(screen.getByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(client.operationsOverview).not.toHaveBeenCalled()
})

it('does not request an unselected park, including royal', () => {
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ client, user: makeUser({ role: 'royal' }), selectedPark: null }))
  expect(screen.getByText('Парк не выбран')).toBeVisible()
  expect(client.operationsOverview).not.toHaveBeenCalled()
})

it.each(['park', 'account', 'role', 'permissions', 'assignment', 'queue'])('synchronously removes old data on %s change and rejects late answers', async change => {
  const pending = deferred<ReturnType<typeof snapshot>>()
  const client = { operationsOverview: vi.fn().mockResolvedValueOnce(snapshot()).mockImplementation(() => pending.promise) }
  const user = makeUser()
  const view = render(tree({ user, client }))
  await screen.findByText('Проверить колесо')
  const nextUser = change === 'account' ? { ...user, id: 9 } : change === 'role' ? { ...user, role: 'mechanic' } : change === 'permissions' ? { ...user, permissions: ['nav.dashboard', 'tracker.read'] } : change === 'assignment' ? { ...user, parks: [park] } : user
  const selectedPark = change === 'park' ? otherPark : change === 'queue' ? { ...park, tracker_queue: 'OTHER' } : park
  const paints: boolean[] = []
  view.rerender(tree({ user: nextUser, selectedPark, client, onRender: () => { paints.push(Boolean(screen.queryByText('Проверить колесо'))) } }))
  expect(paints).not.toContain(true)
  await act(async () => pending.resolve(snapshot({ park_id: selectedPark.id, tasks: [] })))
  expect(screen.queryByText('Проверить колесо')).not.toBeInTheDocument()
})

it.each([401, 403])('revalidation %s clears protected data and refreshes auth once', async status => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn().mockResolvedValueOnce(snapshot()).mockRejectedValue(new ApiError(status, null, 'denied')) }
  const view = render(tree({ user, refreshUser, client }))
  await screen.findByText('Проверить колесо')
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
  fireEvent.focus(window)
  expect(await screen.findByRole('heading', { name: status === 403 ? 'Нет доступа' : 'Сессия истекла' })).toBeVisible()
  expect(screen.queryByText('Проверить колесо')).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
  view.rerender(tree({ user: { ...user }, refreshUser, client }))
  expect(refreshUser).toHaveBeenCalledTimes(1)
  expect(client.operationsOverview).toHaveBeenCalledTimes(2)
})

it.each([401, 403])('released A cannot deny B or poison remounted A after late %s', async status => {
  const pending = deferred<ReturnType<typeof snapshot>>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { operationsOverview: vi.fn().mockImplementationOnce(() => pending.promise).mockResolvedValue(snapshot({ tasks: [] })) }
  const a = render(tree({ user, refreshUser, client }))
  await waitFor(() => expect(client.operationsOverview).toHaveBeenCalledTimes(1))
  a.unmount()
  const b = render(tree({ user: { ...user, id: 20 }, refreshUser, client }))
  await screen.findByText('Нет задач в выбранных статусах')
  b.unmount()
  render(tree({ user, refreshUser, client }))
  await screen.findByText('Нет задач в выбранных статусах')
  await act(async () => pending.reject(new ApiError(status)))
  expect(refreshUser).not.toHaveBeenCalled()
  expect(client.operationsOverview).toHaveBeenCalledTimes(3)
  expect(screen.getByText('Нет задач в выбранных статусах')).toBeVisible()
})

it('released pending success cannot repopulate A cache and StrictMode still loads', async () => {
  const pending = deferred<ReturnType<typeof snapshot>>()
  const client = { operationsOverview: vi.fn().mockImplementationOnce(() => pending.promise).mockResolvedValue(snapshot({ tasks: [] })) }
  const first = render(tree({ client }))
  first.unmount()
  await act(async () => pending.resolve(snapshot()))
  const paints: boolean[] = []
  render(tree({ client, strict: true, onRender: () => paints.push(Boolean(screen.queryByText('Проверить колесо'))) }))
  await screen.findByText('Нет задач в выбранных статусах')
  expect(paints).not.toContain(true)
  expect(Object.keys(localStorage).filter(key => key.includes('operations:'))).toEqual([])
})

it('retains only same-owner data after offline revalidation and retries', async () => {
  const client = { operationsOverview: vi.fn().mockResolvedValueOnce(snapshot()).mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(snapshot({ tasks: [] })) }
  render(tree({ client }))
  await screen.findByText('Проверить колесо')
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
  fireEvent.focus(window)
  await screen.findByText('Нет сети')
  expect(screen.getByText('Проверить колесо')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await screen.findByText('Нет задач в выбранных статусах')
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })
