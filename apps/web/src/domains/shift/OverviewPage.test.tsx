import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, type OperationsOverview, type Park, type User } from '../../api'
import { ParkScopeContext } from '../../app/park/parkScope'
import { AuthContext } from '../../auth-context'
import { resourceStore } from '../../lib/resource'
import { deferred, makeUser, park, snapshot } from '../insights/operations.test-support'
import { OverviewPage } from './OverviewPage'

function Location() { return <output aria-label="URL">{useLocation().search}</output> }

type TreeOptions = {
  user?: User
  selectedPark?: Park | null
  client?: { operationsOverview: (parkId: number, days: number, status: string) => Promise<OperationsOverview> }
  refreshUser?: () => Promise<User>
  url?: string
}

function tree({
  user = makeUser(),
  selectedPark = park as Park | null,
  client = { operationsOverview: vi.fn(async () => snapshot()) },
  refreshUser = vi.fn(async () => user),
  url = '/overview?park=7',
}: TreeOptions = {}) {
  return <MemoryRouter initialEntries={[url]}><AuthContext.Provider value={{ user, loading: false, login: async () => user, refreshUser, logout: async () => {} }}><ParkScopeContext.Provider value={{ parkId: selectedPark?.id ?? null, selectedPark, parks: user.parks, loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => {} }}><OverviewPage apiClient={client} /><Location /></ParkScopeContext.Provider></AuthContext.Provider></MemoryRouter>
}

afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks() })

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

it('shows unknown flow counts instead of treating no observed intervals as zero', async () => {
  const flow = { ...snapshot().flow, observed_buckets: 0, complete: false, points: [] }
  const client = { operationsOverview: vi.fn(async () => snapshot({ counts: { ...snapshot().counts, all: 1 }, flow })) }
  render(tree({ client }))

  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  expect(within(screen.getByLabelText('Сводка потока задач')).getAllByText('Нет данных')).toHaveLength(2)
})

it('normalizes a driver stale status URL before requesting overview data', async () => {
  const client = { operationsOverview: vi.fn(async () => snapshot()) }
  render(tree({ user: makeUser({ role: 'driver' }), client, url: '/overview?park=7&status=queued' }))

  await screen.findByRole('link', { name: 'Открыть задачу RP-1' })
  expect(client.operationsOverview).toHaveBeenCalledWith(7, 7, 'all')
  expect(screen.getByLabelText('URL')).toHaveTextContent('?park=7')
})

it('lets privileged roles clear a selected status back to all permitted tasks', async () => {
  const client = { operationsOverview: vi.fn(async (_park: number, _days: number, status: string) => snapshot({ selected_status: status })) }
  render(tree({ client, url: '/overview?park=7&status=moving' }))

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
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
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
  fireEvent.click(screen.getByRole('button', { name: 'Обновить данные' }))
  expect(await screen.findByRole('heading', { name: status === 403 ? 'Нет доступа' : 'Сессия истекла' })).toBeVisible()
  expect(screen.queryByRole('link', { name: 'Открыть задачу RP-1' })).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
})
