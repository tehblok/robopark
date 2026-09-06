import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Profiler } from 'react'
import { MemoryRouter, useSearchParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type RobotRegistry, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { RobotRegistryList } from './RobotRegistryList'

const user: User = { id: 1, role: 'mechanic', username: 'm', access_status: 'approved', parks: [], permissions: ['tracker.read', 'nav.robot_search'] }
const empty: RobotRegistry = { items: [], total: 0, offset: 0, limit: 50, has_more: false, partial: false, source_complete: true, source: 'scoped_tracker_issues', park_id: 7 }
const row: RobotRegistry['items'][number] = { vin: 'YASADR00000000447', short_number: '447', park_ids: [7], task_keys: ['ROBOPARK-1'], issue_keys: ['ROBOPARK-1'], task_count: 1, error_count: null, telemetry: null, state: 'unknown' }
const secondPage: RobotRegistry = { ...empty, items: [row], offset: 50, total: 51 }
function tree(client: { robotRegistry: (params: unknown) => Promise<RobotRegistry> }, principal = user, refreshUser = vi.fn(async () => principal)) {
  return <MemoryRouter><AuthContext.Provider value={{ user: principal, loading: false, login: async () => principal, logout: async () => undefined, refreshUser }}>
    <RobotRegistryList apiClient={client} parkId={7} scopeLoading={false} />
  </AuthContext.Provider></MemoryRouter>
}

function ScopedRegistry({ client, scopeLoading = false }: { client: Parameters<typeof tree>[0]; scopeLoading?: boolean }) {
  const [params, setParams] = useSearchParams()
  return <>
    <output aria-label="Registry URL">{params.toString()}</output>
    <button onClick={() => { const next = new URLSearchParams(params); next.set('park', '8'); setParams(next) }}>Другой парк</button>
    <RobotRegistryList apiClient={client} parkId={scopeLoading ? null : Number(params.get('park'))} scopeLoading={scopeLoading} />
  </>
}

function scopedTree(client: Parameters<typeof tree>[0], onRender = () => undefined, scopeLoading = false) {
  return <MemoryRouter initialEntries={['/robots?park=7&offset=50&query=447&open_tasks=true']}>
    <AuthContext.Provider value={{ user, loading: false, login: async () => user, logout: async () => undefined, refreshUser: async () => user }}>
      <Profiler id="registry" onRender={onRender}><ScopedRegistry client={client} scopeLoading={scopeLoading} /></Profiler>
    </AuthContext.Provider>
  </MemoryRouter>
}

beforeEach(() => { vi.useFakeTimers({ toFake: ['Date'] }); vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true) })
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('registry resource states', () => {
  it('shows loading then a stable empty result and no enabled pagination', async () => {
    let resolve!: (value: RobotRegistry) => void
    const client = { robotRegistry: vi.fn(() => new Promise<RobotRegistry>(done => { resolve = done })) }
    render(tree(client))
    expect(screen.getByText('Загружаем реестр роботов')).toBeVisible()
    resolve(empty)
    expect(await screen.findByRole('heading', { name: 'Роботы не найдены' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Назад' })).toBeDisabled()
  })

  it('retries an upstream failure and displays the new batch', async () => {
    const client = { robotRegistry: vi.fn().mockRejectedValueOnce(new ApiError(502, 'tracker_upstream_error')).mockResolvedValue(empty) }
    render(tree(client))
    expect(await screen.findByRole('alert')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
    expect(await screen.findByRole('heading', { name: 'Роботы не найдены' })).toBeVisible()
    expect(client.robotRegistry).toHaveBeenCalledTimes(2)
  })

  it('does not read Tracker without permission and refreshes an expired session once', async () => {
    const client = { robotRegistry: vi.fn().mockRejectedValue(new ApiError(401, 'expired')) }
    const refresh = vi.fn(async () => user)
    const view = render(tree(client, { ...user, permissions: [] }, refresh))
    expect(screen.getByRole('heading', { name: 'Реестр недоступен' })).toBeVisible()
    expect(client.robotRegistry).not.toHaveBeenCalled()
    view.rerender(tree(client, user, refresh))
    expect(await screen.findByRole('heading', { name: 'Сессия истекла' })).toBeVisible()
    fireEvent.change(screen.getByLabelText('Поиск по номеру, VIN или задаче'), { target: { value: '447' } })
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
    expect(client.robotRegistry).toHaveBeenCalledTimes(1)
  })

  it('retains a valid deep-linked page but resets it on a park URL transition', async () => {
    const client = { robotRegistry: vi.fn().mockResolvedValueOnce(secondPage).mockResolvedValue({ ...empty, park_id: 8, items: [row], total: 1 }) }
    render(scopedTree(client))
    expect(await screen.findByText('51–51 из 51')).toBeVisible()
    expect(client.robotRegistry).toHaveBeenCalledWith(expect.objectContaining({ park_id: 7, offset: 50 }))
    fireEvent.click(screen.getByRole('button', { name: 'Другой парк' }))
    expect(await screen.findByText('1–1 из 1')).toBeVisible()
    expect(screen.getByLabelText('Registry URL')).toHaveTextContent('park=8&query=447&open_tasks=true')
    expect(client.robotRegistry).toHaveBeenCalledTimes(2)
    expect(client.robotRegistry).toHaveBeenLastCalledWith(expect.objectContaining({ park_id: 8, offset: 0, query: '447', open_tasks: true }))
  })

  it('retains the settled page across transient park loading without requesting the all-parks scope', async () => {
    const client = { robotRegistry: vi.fn().mockResolvedValue(secondPage) }
    const view = render(scopedTree(client))
    expect(await screen.findByText('51–51 из 51')).toBeVisible()
    view.rerender(scopedTree(client, undefined, true))
    expect(screen.getByText('Загружаем реестр роботов')).toBeVisible()
    expect(client.robotRegistry).toHaveBeenCalledTimes(1)
    view.rerender(scopedTree(client))
    expect(await screen.findByText('51–51 из 51')).toBeVisible()
    expect(client.robotRegistry).toHaveBeenLastCalledWith(expect.objectContaining({ park_id: 7, offset: 50 }))
    expect(screen.getByLabelText('Registry URL')).toHaveTextContent('offset=50')
  })

  it('clamps a now-out-of-range page without committing a false empty result or reversed range', async () => {
    const committed: string[] = []
    const client = { robotRegistry: vi.fn().mockResolvedValueOnce(secondPage).mockResolvedValueOnce({ ...empty, offset: 50, total: 1 }).mockResolvedValue({ ...empty, items: [row], total: 1 }) }
    render(scopedTree(client, () => { committed.push(document.body.textContent ?? '') }))
    expect(await screen.findByText('51–51 из 51')).toBeVisible()
    vi.advanceTimersByTime(30_000); fireEvent(document, new Event('visibilitychange'))
    expect(await screen.findByText('1–1 из 1')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Открыть робота 447' })).toBeVisible()
    expect(client.robotRegistry).toHaveBeenCalledTimes(3)
    expect(client.robotRegistry).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 }))
    expect(screen.getByLabelText('Registry URL')).not.toHaveTextContent('offset=')
    expect(committed.some(text => text.includes('Роботы не найдены') || text.includes('51–50'))).toBe(false)
  })

  it('resets pagination when the total shrinks to zero and disables both page actions', async () => {
    const client = { robotRegistry: vi.fn().mockResolvedValueOnce(secondPage).mockResolvedValue({ ...empty, offset: 50 }) }
    render(scopedTree(client))
    expect(await screen.findByText('51–51 из 51')).toBeVisible()
    vi.advanceTimersByTime(30_000); fireEvent(document, new Event('visibilitychange'))
    expect(await screen.findByRole('heading', { name: 'Роботы не найдены' })).toBeVisible()
    expect(screen.getByLabelText('Registry URL')).not.toHaveTextContent('offset=')
    expect(screen.getByText('0 роботов')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Назад' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Далее' })).toBeDisabled()
  })
})

it('reuses a fresh registry batch across filter round trips and resumes stale data automatically', async () => {
  const client = { robotRegistry: vi.fn().mockResolvedValue({ ...empty, items: [row], total: 1 }) }
  render(tree(client)); await screen.findByRole('link', { name: 'Открыть робота 447' })
  expect(screen.queryByRole('button', { name: /Обновить/ })).not.toBeInTheDocument()
  const search = screen.getByLabelText('Поиск по номеру, VIN или задаче')
  fireEvent.change(search, { target: { value: '447' } }); await act(async () => undefined)
  fireEvent.change(search, { target: { value: '' } }); await act(async () => undefined)
  expect(client.robotRegistry).toHaveBeenCalledTimes(2)
  vi.advanceTimersByTime(30_000); fireEvent(document, new Event('visibilitychange')); await act(async () => undefined)
  expect(client.robotRegistry).toHaveBeenCalledTimes(3)
})

it('suspends registry requests offline or hidden and coalesces wake events', async () => {
  vi.useFakeTimers()
  const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  const client = { robotRegistry: vi.fn().mockResolvedValue(empty) }
  render(tree(client)); await act(async () => undefined)
  await act(async () => vi.advanceTimersByTimeAsync(60_000))
  expect(client.robotRegistry).not.toHaveBeenCalled()
  online.mockReturnValue(true); fireEvent(window, new Event('online')); await act(async () => undefined)
  expect(client.robotRegistry).toHaveBeenCalledTimes(1)
  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
  fireEvent(document, new Event('visibilitychange'))
  await act(async () => vi.advanceTimersByTimeAsync(60_000))
  expect(client.robotRegistry).toHaveBeenCalledTimes(1)
  let done!: (value: RobotRegistry) => void
  client.robotRegistry.mockImplementation(() => new Promise(resolve => { done = resolve }))
  hidden.mockReturnValue(false)
  fireEvent(document, new Event('visibilitychange')); fireEvent(window, new Event('online'))
  await act(async () => undefined)
  expect(client.robotRegistry).toHaveBeenCalledTimes(2)
  await act(async () => vi.advanceTimersByTimeAsync(60_000))
  expect(client.robotRegistry).toHaveBeenCalledTimes(2)
  await act(async () => done(empty))
})
