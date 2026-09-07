import { StrictMode } from 'react'
import { beforeEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type User, type Park } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { resourceStore } from '../../lib/resource'
import { Analytics } from '../../pages/Analytics'
import { deferred, makeUser, otherPark, park } from '../insights/operations.test-support'
import { analyticsFixture as fixture } from './analytics.test-support'
type Client = { analytics: (parkId: number, days: number, bucket: '2h' | '1d') => Promise<ReturnType<typeof fixture>> }
function Location() { return <output aria-label="URL">{useLocation().search}</output> }
function tree({ client, url = '/analytics?park=7', user = makeUser(), selectedPark = park, parks = [park, otherPark], allowAllParks = false, refreshUser = vi.fn(async () => user) }: { client: Client; url?: string; user?: User; selectedPark?: Park | null; parks?: Park[]; allowAllParks?: boolean; refreshUser?: () => Promise<User> }) {
  return <MemoryRouter initialEntries={[url]}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser, logout: async () => {} }}><ParkScopeContext.Provider value={{ selectedPark, parkId: selectedPark?.id ?? null, parks, allowAllParks, loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><Analytics apiClient={client} /><Location /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}
afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks(); vi.useRealTimers() })

it('loads historical analytics with trends, coverage and genuine task drilldowns', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client }))
  expect(await screen.findByRole('heading', { name: 'Динамика процесса' })).toBeVisible()
  for (const heading of ['Возраст незавершённых задач', 'Динамика SLA', 'Наблюдаемая длительность этапов', 'Нагрузка по этапам']) {
    expect(screen.getByRole('heading', { name: heading })).toBeVisible()
  }
  expect(screen.queryByRole('heading', { name: 'Текущие задачи' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Статус задач')).not.toBeInTheDocument()
  expect(screen.getByText(/Неполная история/)).toBeVisible()
  expect(screen.getAllByText('Нет наблюдений').length).toBeGreaterThan(0)
  const drilldown = screen.getByText('Задачи в наблюдениях (1)')
  fireEvent.click(drilldown)
  expect(within(drilldown.closest('details')!).getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
  expect(client.analytics).toHaveBeenCalledWith(7, 7, '1d')
})

it('keeps historical filters independent of overview and compares only accessible parks', async () => {
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(tree({ client, url: '/analytics?park=7&days=30&status=moving' }))
  await screen.findByRole('heading', { name: 'Динамика процесса' })
  expect(client.analytics).toHaveBeenCalledWith(7, 7, '1d')
  fireEvent.change(screen.getByLabelText('Период аналитики'), { target: { value: '30' } })
  await waitFor(() => expect(client.analytics).toHaveBeenLastCalledWith(7, 30, '1d'))
  fireEvent.change(screen.getByLabelText('Шаг графиков'), { target: { value: '2h' } })
  await waitFor(() => expect(client.analytics).toHaveBeenLastCalledWith(7, 30, '2h'))
  fireEvent.change(screen.getByLabelText('Сравнить с парком'), { target: { value: '8' } })
  await waitFor(() => expect(client.analytics).toHaveBeenCalledWith(8, 30, '2h'))
  expect(await screen.findByRole('region', { name: 'История парка Юг' })).toBeVisible()
  expect(screen.getByRole('table', { name: 'Сравнение парков' })).toBeVisible()
  expect(screen.getByLabelText('URL')).toHaveTextContent('park=7&period=30&bucket=2h&compare=8')
  expect(screen.getByLabelText('URL')).not.toHaveTextContent('status=')
})

it('normalizes an unauthorized comparison without issuing its request', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client, user: makeUser({ parks: [park] }), url: '/analytics?park=7&compare=8&period=999&bucket=5m' }))
  await screen.findByRole('heading', { name: 'Динамика процесса' })
  expect(client.analytics).toHaveBeenCalledTimes(1)
  expect(client.analytics).toHaveBeenCalledWith(7, 7, '1d')
  expect(within(screen.getByLabelText('Сравнить с парком')).queryByText('Юг')).not.toBeInTheDocument()
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=7')
})

it.each(['nav.analytics', 'tracker.read'])('denies analytics without %s', permission => {
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client, user: makeUser({ permissions: ['nav.analytics', 'tracker.read'].filter(item => item !== permission) }) }))
  expect(screen.getByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(client.analytics).not.toHaveBeenCalled()
})

it('drops another park’s in-flight result and refreshes authorization once on 403', async () => {
  const pending = deferred<ReturnType<typeof fixture>>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => ({ ...user }))
  const client = { analytics: vi.fn().mockImplementationOnce(() => pending.promise).mockRejectedValue(new ApiError(403)) }
  const view = render(tree({ client, user, refreshUser }))
  view.rerender(tree({ client, user, selectedPark: otherPark, refreshUser }))
  expect(await screen.findByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  await act(async () => pending.resolve(fixture()))
  expect(screen.queryByRole('heading', { name: 'Динамика процесса' })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
  expect(client.analytics).toHaveBeenCalledTimes(2)
})

it('recovers after disabling a comparison whose deferred request returned 403', async () => {
  const denied = deferred<ReturnType<typeof fixture>>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { analytics: vi.fn((id: number) => id === 8 ? denied.promise : Promise.resolve(fixture(id))) }
  render(tree({ client, user, refreshUser, url: '/analytics?park=7&compare=8' }))
  await act(async () => denied.reject(new ApiError(403)))
  expect(await screen.findByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  fireEvent.change(screen.getByLabelText('Сравнить с парком'), { target: { value: '' } })
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 7])
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it('recovers on an accessible primary park after another park returned 403', async () => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { analytics: vi.fn(async (id: number) => { if (id === 7) throw new ApiError(403); return fixture(id) }) }
  const view = render(tree({ client, user, refreshUser }))
  await screen.findByRole('heading', { name: 'Нет доступа' })
  view.rerender(tree({ client, user, refreshUser, selectedPark: otherPark }))
  expect(await screen.findByRole('region', { name: 'История парка Юг' })).toBeVisible()
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8])
})

it('does not retry unchanged denied access but recovers after refreshed access changes', async () => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => ({ ...user }))
  const client = { analytics: vi.fn().mockRejectedValueOnce(new ApiError(403)).mockResolvedValue(fixture()) }
  const view = render(tree({ client, user, refreshUser }))
  await screen.findByRole('heading', { name: 'Нет доступа' })
  view.rerender(tree({ client, refreshUser, user: { ...user, permissions: [...user.permissions!].reverse(), parks: [...user.parks].reverse() } }))
  await act(async () => {})
  expect(screen.getByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(1)
  view.rerender(tree({ client, refreshUser, user: { ...user, permissions: [...user.permissions!, 'reports.create'] } }))
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it('recovers after a denied filter context is changed', async () => {
  const client = { analytics: vi.fn(async (id: number, days: number) => { if (days === 7) throw new ApiError(403); return fixture(id, days) }) }
  render(tree({ client }))
  await screen.findByRole('heading', { name: 'Нет доступа' })
  fireEvent.change(screen.getByLabelText('Период аналитики'), { target: { value: '1' } })
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(2)
})

it('ignores a released comparison denial after the active context already recovered', async () => {
  const denied = deferred<ReturnType<typeof fixture>>()
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { analytics: vi.fn((id: number) => id === 8 ? denied.promise : Promise.resolve(fixture(id))) }
  render(tree({ client, user, refreshUser, url: '/analytics?park=7&compare=8' }))
  fireEvent.change(screen.getByLabelText('Сравнить с парком'), { target: { value: '' } })
  await screen.findByRole('region', { name: 'История парка Север' })
  await act(async () => denied.reject(new ApiError(403)))
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
  expect(refreshUser).not.toHaveBeenCalled()
})


it('reuses fresh history on remount and refreshes stale history on focus', async () => {
  const client = { analytics: vi.fn(async () => fixture()) }
  const view = render(tree({ client }))
  await screen.findByRole('region', { name: 'История парка Север' })
  expect(screen.queryByRole('button', { name: /Обновить/ })).not.toBeInTheDocument()
  view.unmount()
  render(tree({ client }))
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(1)
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await waitFor(() => expect(client.analytics).toHaveBeenCalledTimes(2))
  expect(Object.keys(localStorage).filter(key => key.includes('analytics:'))).toEqual([])
})

it.each([401, 403])('clears cached history after automatic %s revalidation and halts retries', async status => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const client = { analytics: vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValue(new ApiError(status)) }
  render(tree({ client, user, refreshUser }))
  await screen.findByRole('region', { name: 'История парка Север' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await screen.findByRole('heading', { name: status === 401 ? 'Сессия истекла' : 'Нет доступа' })
  expect(screen.queryByRole('region', { name: 'История парка Север' })).not.toBeInTheDocument()
  fireEvent.focus(window)
  fireEvent(window, new Event('online'))
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it('keeps an expired session denied when the selected park changes', async () => {
  const client = { analytics: vi.fn().mockRejectedValue(new ApiError(401)) }
  const view = render(tree({ client }))
  await screen.findByRole('heading', { name: 'Сессия истекла' })
  view.rerender(tree({ client, selectedPark: otherPark }))
  expect(screen.getByRole('heading', { name: 'Сессия истекла' })).toBeVisible()
  expect(client.analytics).toHaveBeenCalledTimes(1)
})

it('keeps historical content during an offline background refresh and allows retry', async () => {
  const client = { analytics: vi.fn().mockResolvedValueOnce(fixture()).mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue(fixture()) }
  render(tree({ client }))
  await screen.findByRole('region', { name: 'История парка Север' })
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await screen.findByText('Нет сети')
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await waitFor(() => expect(screen.queryByText('Нет сети')).not.toBeInTheDocument())
  expect(client.analytics).toHaveBeenCalledTimes(3)
})


it('polls historical analytics every two minutes without clearing the loaded view', async () => {
  vi.useFakeTimers()
  const client = { analytics: vi.fn(async () => fixture()) }
  render(tree({ client }))
  await act(async () => {})
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  await act(async () => { await vi.advanceTimersByTimeAsync(119_999) })
  expect(client.analytics).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
})


it.each([200, 403])('does not reuse a retired pending request after remount when it later returns %s', async status => {
  const pending = deferred<ReturnType<typeof fixture>>()
  const refreshUser = vi.fn(async () => makeUser())
  const client = { analytics: vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue(fixture()) }
  const first = render(tree({ client, refreshUser }))
  first.unmount()
  render(tree({ client, refreshUser }))
  await screen.findByRole('region', { name: 'История парка Север' })
  await act(async () => { if (status === 403) pending.reject(new ApiError(403)); else pending.resolve(fixture()) })
  expect(client.analytics).toHaveBeenCalledTimes(2)
  expect(refreshUser).not.toHaveBeenCalled()
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
})

// Keep lifecycle assertions deterministic; pollingCapacity tests exercise jitter.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })


it.each(['admin', 'royal', 'operator'] as const)('shows every accessible active park for %s in all mode', async role => {
  const hiddenPark = { ...park, id: 9, name: 'Недоступный' }
  const inactivePark = { ...park, id: 10, name: 'Неактивный', is_active: false }
  const user = makeUser({ role, parks: [park, otherPark] })
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(tree({ client, user, selectedPark: null, allowAllParks: true, parks: [park, otherPark, hiddenPark, inactivePark], url: '/analytics?park=all&compare=8' }))
  expect(await screen.findByRole('table', { name: 'Сравнение парков' })).toBeVisible()
  expect(screen.getByRole('heading', { name: 'Все доступные парки' })).toBeVisible()
  expect(screen.queryByLabelText('Сравнить с парком')).not.toBeInTheDocument()
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=all')
  expect(screen.getByLabelText('URL')).not.toHaveTextContent('compare=')
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual(role === 'operator' ? [7, 8] : [7, 8, 9])
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.getByRole('region', { name: 'История парка Юг' })).toBeVisible()
})

it('does not request any park for an operator with no assigned parks in all mode', () => {
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(tree({ client, user: makeUser({ role: 'operator', parks: [] }), selectedPark: null, allowAllParks: true }))
  expect(screen.getByRole('heading', { name: 'Нет доступных парков' })).toBeVisible()
  expect(client.analytics).not.toHaveBeenCalled()
})

it('bounds all-park loading to three requests and preserves park ordering', async () => {
  const parks = Array.from({ length: 5 }, (_, i) => ({ ...park, id: i + 7, name: `Парк ${i + 7}` }))
  const pending = parks.map(() => deferred<ReturnType<typeof fixture>>())
  const client = { analytics: vi.fn((id: number) => pending[id - 7].promise) }
  render(tree({ client, user: makeUser({ role: 'admin' }), parks, selectedPark: null, allowAllParks: true }))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9])
  await act(async () => pending[1].resolve(fixture(8)))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9, 10])
  await act(async () => pending[0].resolve(fixture(7)))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9, 10, 11])
  await act(async () => { pending.slice(2).forEach((item, i) => item.resolve(fixture(i + 9))) })
  expect(screen.getAllByRole('region', { name: /История парка/ }).map(item => item.getAttribute('aria-label'))).toEqual(parks.map(item => `История парка ${item.name}`))
})

it.each([200, 403])('stops queued all-park calls and ignores late %s responses when switching to one park', async status => {
  const parks = Array.from({ length: 5 }, (_, i) => ({ ...park, id: i + 7, name: `Парк ${i + 7}` }))
  const user = makeUser({ role: 'admin' })
  const refreshUser = vi.fn(async () => user)
  const pending = parks.slice(0, 3).map(() => deferred<ReturnType<typeof fixture>>())
  const client = { analytics: vi.fn((id: number) => id === 11 ? Promise.resolve(fixture(id)) : pending[id - 7].promise) }
  const view = render(tree({ client, user, refreshUser, parks, selectedPark: null, allowAllParks: true }))
  view.rerender(tree({ client, user, refreshUser, parks, selectedPark: parks[4], allowAllParks: true }))
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9])
  await act(async () => { pending.forEach((item, i) => status === 403 ? item.reject(new ApiError(403)) : item.resolve(fixture(i + 7))) })
  await screen.findByRole('region', { name: 'История парка Парк 11' })
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9, 11])
  expect(screen.getAllByRole('region', { name: /История парка/ })).toHaveLength(1)
  expect(refreshUser).not.toHaveBeenCalled()
})

it.each([401, 403])('stops queued all-park calls immediately after %s denial', async status => {
  const parks = Array.from({ length: 5 }, (_, i) => ({ ...park, id: i + 7 }))
  const pending = parks.slice(0, 3).map(() => deferred<ReturnType<typeof fixture>>())
  const client = { analytics: vi.fn((id: number) => pending[id - 7].promise) }
  render(tree({ client, user: makeUser({ role: 'admin' }), parks, selectedPark: null, allowAllParks: true }))
  await act(async () => { pending[1].reject(new ApiError(status)); pending[0].resolve(fixture(7)); pending[2].resolve(fixture(9)) })
  expect(await screen.findByRole('heading', { name: status === 401 ? 'Сессия истекла' : 'Нет доступа' })).toBeVisible()
  expect(client.analytics.mock.calls.map(call => call[0])).toEqual([7, 8, 9])
  expect(screen.queryByRole('region', { name: /История парка/ })).not.toBeInTheDocument()
})


it('loads all parks through StrictMode effect replay', async () => {
  const client = { analytics: vi.fn(async (id: number) => fixture(id)) }
  render(<StrictMode>{tree({ client, selectedPark: null, allowAllParks: true })}</StrictMode>)
  expect(await screen.findByRole('region', { name: 'История парка Север' })).toBeVisible()
  expect(screen.getAllByRole('region', { name: /История парка/ })).toHaveLength(2)
})

it('removes cached all-park history when a denial arrives after another park failed', async () => {
  const user = makeUser()
  const refreshUser = vi.fn(async () => user)
  const first = deferred<ReturnType<typeof fixture>>()
  const second = deferred<ReturnType<typeof fixture>>()
  let revalidating = false
  const client = { analytics: vi.fn((id: number) => revalidating ? (id === park.id ? first.promise : second.promise) : Promise.resolve(fixture(id))) }
  render(tree({ client, user, refreshUser, selectedPark: null, allowAllParks: true }))
  await screen.findByRole('region', { name: 'История парка Север' })
  revalidating = true
  vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
  fireEvent.focus(window)
  await waitFor(() => expect(client.analytics).toHaveBeenCalledTimes(4))
  await act(async () => first.reject(new ApiError(500)))
  expect(screen.getByRole('region', { name: 'История парка Север' })).toBeVisible()
  await act(async () => second.reject(new ApiError(403)))
  expect(await screen.findByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  expect(screen.queryByRole('region', { name: /История парка/ })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
})
