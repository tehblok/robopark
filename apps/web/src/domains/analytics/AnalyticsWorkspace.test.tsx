import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { Analytics } from '../../pages/Analytics'
import { deferred, makeUser, otherPark, park } from '../insights/operations.test-support'
import { analyticsFixture as fixture } from './analytics.test-support'
type Client = { analytics: (parkId: number, days: number, bucket: '2h' | '1d') => Promise<ReturnType<typeof fixture>> }
function Location() { return <output aria-label="URL">{useLocation().search}</output> }
function tree({ client, url = '/analytics?park=7', user = makeUser(), selectedPark = park, refreshUser = vi.fn(async () => user) }: { client: Client; url?: string; user?: User; selectedPark?: typeof park; refreshUser?: () => Promise<User> }) {
  return <MemoryRouter initialEntries={[url]}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser, logout: async () => {} }}><ParkScopeContext.Provider value={{ selectedPark, parkId: selectedPark.id, parks: [park, otherPark], loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><Analytics apiClient={client} /><Location /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}
afterEach(() => vi.restoreAllMocks())

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
