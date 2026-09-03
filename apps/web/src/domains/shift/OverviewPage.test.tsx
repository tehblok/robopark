import { Profiler, useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  api,
  ApiError,
  ApiTimeoutError,
  type DashboardSummary,
  type Park,
  type User,
} from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeProvider } from '../../app/park/ParkScopeProvider'
import { ParkScopeContext } from '../../app/park/parkScope'
import { resourceStore, resetCoalescingForTests } from '../../lib/resource'
import type { OverviewApiClient, OverviewPayload } from './overviewData'
import { OverviewPage } from './OverviewPage'

const park: Park = {
  id: 7,
  name: 'Север',
  tag: 'Alpha',
  tracker_queue: 'ROBOPARK',
  is_active: true,
}

const summary: DashboardSummary = {
  park_id: park.id,
  generated_at: '2026-09-02T09:00:00Z',
  arrived: 2,
  done: 4,
  queued: 3,
  in_transit: 1,
  moving: [{ key: 'ROBOPARK-42', summary: 'Робот 447 остановился' }],
}

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 3,
    username: 'operator',
    role: 'operator',
    access_status: 'approved',
    permissions: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'tracker.read'],
    parks: [park],
    ...overrides,
  }
}

function client(overrides: Partial<OverviewApiClient> = {}): OverviewApiClient {
  return {
    dashboardSummary: vi.fn(async () => summary),
    mechanicTasks: vi.fn(async () => ({
      park_tag: park.tag,
      status: 'all',
      counts: {},
      items: [],
    })),
    operatorBlockers: vi.fn(async () => ({
      park_id: park.id,
      park_tag: park.tag,
      status: 'all',
      counts: {},
      items: [],
    })),
    trackerIssues: vi.fn(async () => ({
      items: [],
      total: 0,
      limit: 5,
      offset: 0,
      has_more: false,
    })),
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function scopedOverviewTree(user: User, selectedPark: Park, apiClient: OverviewApiClient,
  refreshUser = vi.fn(async () => user), onRender = () => {}) {
  return <MemoryRouter><AuthContext.Provider value={{ user, loading: false,
    refreshUser, login: async () => user, logout: async () => undefined }}>
    <ParkScopeContext.Provider value={{ selectedPark, parkId: selectedPark.id, parks: user.parks,
      loading: false, locked: false, setParkId: vi.fn(), refreshParks: async () => undefined }}>
      <Profiler id="access" onRender={onRender}><OverviewPage apiClient={apiClient} /></Profiler>
    </ParkScopeContext.Provider>
  </AuthContext.Provider></MemoryRouter>
}

function cacheKey(user: User, parkId: number | null = park.id): string {
  const scope = (item: Park) => [item.id, item.tag?.trim(), item.tracker_queue?.trim(), item.is_active !== false]
  const parks = [...user.parks].sort((a, b) => a.id - b.id)
  const selected = parks.find((item) => item.id === parkId)
  const keyScope = user.role === 'royal'
    ? `fleet:${parks.filter((item) => item.is_active !== false).map((item) => item.id).join(',')}` : parkId
  return `overview:${user.id}:${user.role}:${keyScope}:${JSON.stringify([
    user.username, user.tracker_login, user.access_status, Boolean(user.must_change_password),
    [...new Set(user.permissions ?? [])].sort(), parks.map(scope), parks.map(scope),
    user.role !== 'royal' && selected ? scope(selected) : null,
  ])}`
}

function payload(): OverviewPayload {
  return { kind: 'park', park, summary, issues: [] }
}

function overviewTree({
  user,
  apiClient,
  refreshUser = vi.fn(async () => user),
  parkId = user.parks[0]?.id ?? null,
  onRender = () => undefined,
}: {
  user: User
  apiClient: OverviewApiClient
  refreshUser?: () => Promise<User>
  parkId?: number | null
  onRender?: () => void
}) {
  return (
    <MemoryRouter initialEntries={[`/overview${parkId == null ? '' : `?park=${parkId}`}`]}>
      <AuthContext.Provider value={{
        user,
        loading: false,
        login: async () => user,
        refreshUser,
        logout: async () => undefined,
      }}>
        <ParkScopeProvider>
          <Profiler id="overview" onRender={onRender}>
            <OverviewPage apiClient={apiClient} />
          </Profiler>
        </ParkScopeProvider>
      </AuthContext.Provider>
    </MemoryRouter>
  )
}

function renderOverview({
  user = makeUser(),
  apiClient = client(),
  refreshUser = vi.fn(async () => user),
  parkId = user.parks[0]?.id ?? null,
}: {
  user?: User
  apiClient?: OverviewApiClient
  refreshUser?: () => Promise<User>
  parkId?: number | null
} = {}) {
  return {
    ...render(overviewTree({ user, apiClient, refreshUser, parkId })),
    user,
    apiClient,
    refreshUser,
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date('2026-09-02T09:03:00Z'))
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  resourceStore.clearAll()
  resetCoalescingForTests()
  sessionStorage.clear()
})

describe('OverviewPage', () => {
  it.each(['permissions', 'read-revoked', 'tag', 'queue'] as const)(
    'suppresses old scope cache after a same-ID %s change, including remount', async (change) => {
      const user = makeUser({ role: 'field_lead' })
      const apiClient = client()
      const commits: string[] = []
      const record = () => { commits.push(document.body.textContent ?? '') }
      const view = render(scopedOverviewTree(user, park, apiClient, undefined, record))
      await screen.findByTestId('overview-scope')
      expect(document.body).toHaveTextContent('ROBOPARK-42')
      const pending = deferred<DashboardSummary>()
      vi.mocked(apiClient.dashboardSummary).mockImplementation(() => pending.promise)
      const nextPark = { ...park, ...(change === 'tag' ? { tag: 'Beta' } : {}),
        ...(change === 'queue' ? { tracker_queue: 'NEWQUEUE' } : {}) }
      const nextUser = { ...user, parks: [nextPark], permissions: change === 'permissions'
        ? ['nav.dashboard', 'nav.tasks', 'tracker.read']
        : change === 'read-revoked' ? ['nav.dashboard', 'nav.tasks'] : user.permissions }
      const before = commits.length
      view.rerender(scopedOverviewTree(nextUser, nextPark, apiClient, undefined, record))
      expect(commits.slice(before).every((text) => !text.includes('ROBOPARK-42'))).toBe(true)
      view.unmount()
      render(scopedOverviewTree(nextUser, nextPark, apiClient))
      expect(document.body).not.toHaveTextContent('ROBOPARK-42')
      await act(async () => { pending.reject(new TypeError('offline')) })
      expect(await screen.findByText('Нет сети')).toBeInTheDocument()
      expect(document.body).not.toHaveTextContent('ROBOPARK-42')
      if (change === 'read-revoked') expect(apiClient.trackerIssues).toHaveBeenCalledTimes(1)
    },
  )

  it.each(['success', '401'] as const)('ignores an old scope in-flight %s after access changes in StrictMode', async (result) => {
    const pending = deferred<DashboardSummary>()
    const user = makeUser({ role: 'field_lead' })
    const nextUser = { ...user, permissions: ['nav.dashboard', 'nav.tasks'] }
    const apiClient = client({ dashboardSummary: vi.fn().mockImplementationOnce(() => pending.promise)
      .mockResolvedValue({ ...summary, moving: [], arrived: 9 }) })
    const refreshUser = vi.fn(async () => nextUser)
    const view = render(scopedOverviewTree(user, park, apiClient, refreshUser), { reactStrictMode: true })
    view.rerender(scopedOverviewTree(nextUser, park, apiClient, refreshUser))
    expect(await screen.findByTestId('overview-scope')).toBeInTheDocument()
    expect(screen.getByTestId('overview-metrics')).toHaveTextContent('9')
    await act(async () => {
      if (result === 'success') pending.resolve(summary)
      else pending.reject(new ApiError(401))
    })
    expect(screen.getByTestId('overview-metrics')).toHaveTextContent('9')
    expect(document.body).not.toHaveTextContent('ROBOPARK-42')
    expect(apiClient.trackerIssues).not.toHaveBeenCalled()
    expect(refreshUser).not.toHaveBeenCalled()
  })

  it('starts a fresh summary when access returns before its obsolete first load finishes', async () => {
    const pending = deferred<DashboardSummary>()
    const user = makeUser({ role: 'field_lead' })
    const nextUser = { ...user, permissions: ['nav.dashboard', 'nav.tasks'] }
    const apiClient = client({ dashboardSummary: vi.fn().mockImplementationOnce(() => pending.promise)
      .mockResolvedValue({ ...summary, moving: [], arrived: 9 }) })
    const view = render(scopedOverviewTree(user, park, apiClient), { reactStrictMode: true })
    view.rerender(scopedOverviewTree(nextUser, park, apiClient))
    await screen.findByTestId('overview-scope')
    view.rerender(scopedOverviewTree(user, park, apiClient))
    await waitFor(() => expect(apiClient.dashboardSummary).toHaveBeenCalledTimes(3))
    await act(async () => { pending.resolve(summary) })
    expect(screen.getByTestId('overview-metrics')).toHaveTextContent('9')
    expect(document.body).not.toHaveTextContent('ROBOPARK-42')
  })

  it('renders scope, state, risk, action, queue and current metrics in semantic order', async () => {
    const dashboardHistory = vi.spyOn(api, 'dashboardHistory')
    const operatorNowReport = vi.spyOn(api, 'operatorNowReport')
    renderOverview()

    const scope = await screen.findByTestId('overview-scope')
    const state = screen.getByTestId('overview-state')
    const risk = screen.getByTestId('overview-risk')
    const action = screen.getByTestId('overview-action')
    const queue = screen.getByTestId('overview-queue')
    const metrics = screen.getByTestId('overview-metrics')
    for (const [before, after] of [
      [scope, state],
      [state, risk],
      [risk, action],
      [action, queue],
      [queue, metrics],
    ]) {
      expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    }
    expect(screen.getByText('Парк: Север')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Разобрать ROBOPARK-42' })).toHaveAttribute(
      'href',
      '/work/ROBOPARK-42?park=7&queue=ROBOPARK',
    )
    expect(screen.queryByText(/истори|тренд/i)).not.toBeInTheDocument()
    expect(dashboardHistory).not.toHaveBeenCalled()
    expect(operatorNowReport).not.toHaveBeenCalled()
  })

  it('shows a local retryable error with its request id and recovers on retry', async () => {
    const dashboardSummary = vi.fn()
      .mockRejectedValueOnce(new ApiError(502, null, 'req-overview'))
      .mockResolvedValueOnce(summary)
    renderOverview({ apiClient: client({ dashboardSummary }) })

    expect(await screen.findByText('Сервис временно недоступен')).toBeInTheDocument()
    expect(screen.getByText(/req-overview/)).toBeInTheDocument()
    expect(screen.queryByTestId('overview-scope')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))

    expect(await screen.findByTestId('overview-scope')).toBeInTheDocument()
    expect(dashboardSummary).toHaveBeenCalledTimes(2)
  })

  it('ages the visible freshness without making another operational request', async () => {
    vi.useRealTimers()
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
    vi.setSystemTime(new Date('2026-09-02T09:00:10Z'))
    const apiClient = client()
    const view = renderOverview({ apiClient })

    const scope = await screen.findByTestId('overview-scope')
    expect(scope).toHaveTextContent('Данные актуальны')
    await act(async () => { vi.advanceTimersByTime(30_000) })
    expect(scope).toHaveTextContent('Данные свежие')
    await act(async () => { vi.advanceTimersByTime(300_000) })
    expect(scope).toHaveTextContent('Данные устарели')
    expect(apiClient.dashboardSummary).toHaveBeenCalledOnce()
    expect(apiClient.operatorBlockers).toHaveBeenCalledOnce()
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('keeps a driver robot-first without requiring a park or requesting operational data', async () => {
    const apiClient = client()
    renderOverview({
      apiClient,
      user: makeUser({ role: 'driver', parks: [], permissions: ['nav.dashboard', 'nav.robot_search'] }),
    })

    expect(await screen.findByRole('link', { name: 'Найти или сканировать робота' })).toHaveAttribute('href', '/robots')
    expect(screen.queryByText('Парк не выбран')).not.toBeInTheDocument()
    expect(screen.queryByText(/Данные свежие|Данные актуальны|Данные устарели/)).not.toBeInTheDocument()
    for (const method of Object.values(apiClient)) expect(method).not.toHaveBeenCalled()
  })

  it('does not offer a driver a robot route missing from the manifest capabilities', async () => {
    renderOverview({
      user: makeUser({ role: 'driver', parks: [], permissions: ['nav.dashboard'] }),
    })

    expect(await screen.findByTestId('overview-scope')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Найти или сканировать робота' })).not.toBeInTheDocument()
  })

  it('does not load operational data before a non-driver park is selected', async () => {
    const apiClient = client()
    renderOverview({ apiClient, user: makeUser({ parks: [] }) })

    expect(await screen.findByText('Парк не выбран')).toBeInTheDocument()
    for (const method of Object.values(apiClient)) expect(method).not.toHaveBeenCalled()
  })

  it('waits while the park provider resolves fleet scope', () => {
    const pending = deferred<Park[]>()
    vi.spyOn(api, 'parks').mockReturnValue(pending.promise)
    const apiClient = client()
    const view = renderOverview({ apiClient, user: makeUser({ role: 'admin' }) })

    expect(screen.getByRole('status', { name: 'Загружаем область работы' })).toBeInTheDocument()
    for (const method of Object.values(apiClient)) expect(method).not.toHaveBeenCalled()
    view.unmount()
    pending.resolve([park])
  })

  it('loads each fleet park once while the provider resolves the selected park', async () => {
    const otherPark = { ...park, id: 8, name: 'Юг', tag: 'Beta' }
    const parks = [park, otherPark]
    vi.spyOn(api, 'parks').mockResolvedValue(parks)
    const dashboardSummary = vi.fn(async (parkId: number) => ({ ...summary, park_id: parkId, moving: [] }))
    renderOverview({
      user: makeUser({ role: 'royal', parks }),
      apiClient: client({ dashboardSummary }),
    })

    expect(await screen.findByTestId('overview-scope')).toHaveTextContent('Все доступные активные парки · 2')
    expect(dashboardSummary.mock.calls).toEqual([[7], [8]])
  })

  it('starts a new fleet load when the accessible scope changes during a pending request', async () => {
    const otherPark = { ...park, id: 8, name: 'Юг', tag: 'Beta' }
    const firstUser = makeUser({ role: 'royal' })
    const nextUser = { ...firstUser, parks: [park, otherPark] }
    const parkList = vi.spyOn(api, 'parks')
      .mockResolvedValueOnce([park])
      .mockResolvedValueOnce(nextUser.parks)
    const pending = deferred<DashboardSummary>()
    const dashboardSummary = vi.fn((parkId: number) => parkId === park.id
      ? pending.promise
      : Promise.resolve({ ...summary, park_id: parkId, moving: [] }))
    const apiClient = client({ dashboardSummary })
    const view = render(overviewTree({ user: firstUser, apiClient }))
    await waitFor(() => expect(dashboardSummary).toHaveBeenCalledWith(park.id))

    view.rerender(overviewTree({ user: nextUser, apiClient }))
    await waitFor(() => expect(parkList).toHaveBeenCalledTimes(2))
    await act(async () => {})

    expect(dashboardSummary).toHaveBeenCalledWith(otherPark.id)
    await act(async () => { pending.resolve(summary) })
    expect(await screen.findByTestId('overview-scope')).toHaveTextContent('Все доступные активные парки · 2')
  })

  it.each([
    ['offline', new TypeError('offline'), 'Нет сети'],
    ['timeout', new ApiTimeoutError(30_000), 'Сервис не ответил вовремя'],
    ['server', new ApiError(502, null, 'stale-overview'), 'Сервис временно недоступен'],
  ] as const)('retains protected cache only for a transient %s failure', async (kind, error, title) => {
    const user = makeUser()
    resourceStore.set(cacheKey(user), payload(), true)
    renderOverview({
      user,
      apiClient: client({ dashboardSummary: vi.fn(async () => { throw error }) }),
    })

    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(screen.getByTestId('overview-scope')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Разобрать ROBOPARK-42' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument()
    expect(screen.getByTestId('overview-scope')).toHaveTextContent(
      kind === 'offline' ? 'Нет связи с источником' : 'Данные устарели',
    )
  })

  it.each([401, 403])('suppresses cache, evicts only the user overview prefix and refreshes once for HTTP %s', async (status) => {
    const user = makeUser()
    const refreshUser = vi.fn(async () => user)
    resourceStore.set(cacheKey(user), payload(), true)
    resourceStore.set(`overview:${user.id}:royal:fleet`, { private: true }, true)
    resourceStore.set('overview:99:operator:7', { other: true }, true)
    resourceStore.set(`work:${user.id}:unrelated`, { work: true }, true)
    const clearAll = vi.spyOn(resourceStore, 'clearAll')
    renderOverview({
      user,
      refreshUser,
      apiClient: client({
        dashboardSummary: vi.fn(async () => { throw new ApiError(status, null, 'denied-overview') }),
      }),
    })

    expect(await screen.findByText(status === 401 ? 'Сессия истекла' : 'Нет доступа')).toBeInTheDocument()
    expect(screen.queryByTestId('overview-scope')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /ROBOPARK-42/ })).not.toBeInTheDocument()
    expect(screen.getByText(/denied-overview/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
    expect(resourceStore.get(cacheKey(user))).toBeUndefined()
    expect(resourceStore.get(`overview:${user.id}:royal:fleet`)).toBeUndefined()
    expect(localStorage.getItem(`robopark:res:${cacheKey(user)}`)).toBeNull()
    expect(resourceStore.get('overview:99:operator:7')).toEqual({ other: true })
    expect(resourceStore.get(`work:${user.id}:unrelated`)).toEqual({ work: true })
    expect(clearAll).not.toHaveBeenCalled()
    expect(refreshUser).toHaveBeenCalledOnce()
  })

  it('keeps a denial stable when authorization refresh replaces the same user object', async () => {
    const user = makeUser()
    const refreshCalls = vi.fn()
    const apiClient = client({
      dashboardSummary: vi.fn(async () => { throw new ApiError(403) }),
    })

    function RefreshingSession() {
      const [currentUser, setCurrentUser] = useState(user)
      const [refreshed, setRefreshed] = useState(false)
      const refreshUser = async () => {
        refreshCalls()
        const nextUser = { ...user, parks: [...user.parks] }
        if (refreshCalls.mock.calls.length === 1) {
          setCurrentUser(nextUser)
          setRefreshed(true)
        }
        return nextUser
      }

      return (
        <MemoryRouter initialEntries={['/overview?park=7']}>
          <AuthContext.Provider value={{
            user: currentUser,
            loading: false,
            login: async () => currentUser,
            refreshUser,
            logout: async () => undefined,
          }}>
            <ParkScopeProvider>
              {refreshed ? <span>Сессия обновлена</span> : null}
              <OverviewPage apiClient={apiClient} />
            </ParkScopeProvider>
          </AuthContext.Provider>
        </MemoryRouter>
      )
    }

    render(<RefreshingSession />)
    await screen.findByText('Сессия обновлена')
    await act(async () => {})

    expect(screen.getByText('Нет доступа')).toBeInTheDocument()
    expect(apiClient.dashboardSummary).toHaveBeenCalledOnce()
    expect(refreshCalls).toHaveBeenCalledOnce()
  })

  it('treats a configuration 403 as a local readiness error, not an authorization denial', async () => {
    const user = makeUser()
    const refreshUser = vi.fn(async () => user)
    resourceStore.set(cacheKey(user), payload(), true)
    renderOverview({
      user,
      refreshUser,
      apiClient: client({
        dashboardSummary: vi.fn(async () => {
          throw new ApiError(403, 'tracker_token_not_configured', 'config-overview')
        }),
      }),
    })

    expect(await screen.findByText('Требуется настройка')).toBeInTheDocument()
    expect(screen.queryByTestId('overview-scope')).not.toBeInTheDocument()
    expect(screen.getByText(/config-overview/)).toBeInTheDocument()
    expect(refreshUser).not.toHaveBeenCalled()
    expect(resourceStore.get(cacheKey(user))).toEqual(payload())
  })

  it.each([
    [new ApiError(404), 'Не найдено'],
    [new ApiError(409), 'Данные изменились'],
    [new Error('unknown'), 'Не удалось выполнить действие'],
  ] as const)('does not render cached data for a non-retainable error %s', async (error, title) => {
    const user = makeUser()
    resourceStore.set(cacheKey(user), payload(), true)
    renderOverview({
      user,
      apiClient: client({ dashboardSummary: vi.fn(async () => { throw error }) }),
    })

    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(screen.queryByTestId('overview-scope')).not.toBeInTheDocument()
  })

  it('loads a capable custom dashboard role without a Tracker request', async () => {
    const apiClient = client({
      dashboardSummary: vi.fn(async () => ({ ...summary, moving: [] })),
    })
    renderOverview({
      apiClient,
      user: makeUser({ role: 'field_lead', permissions: ['nav.dashboard'] }),
    })

    expect(await screen.findByText('Что требует внимания сейчас')).toBeInTheDocument()
    expect(apiClient.dashboardSummary).toHaveBeenCalledWith(park.id)
    expect(apiClient.trackerIssues).not.toHaveBeenCalled()
    expect(apiClient.mechanicTasks).not.toHaveBeenCalled()
    expect(apiClient.operatorBlockers).not.toHaveBeenCalled()
    expect(screen.queryByRole('link', { name: 'Открыть работу' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('overview-queue')).not.toBeInTheDocument()
  })

  it('does not call an unrequested queue empty when current counts are non-zero', async () => {
    const apiClient = client({
      dashboardSummary: vi.fn(async () => ({ ...summary, moving: [] })),
    })
    renderOverview({
      apiClient,
      user: makeUser({ role: 'field_lead', permissions: ['nav.dashboard', 'nav.tasks'] }),
    })

    expect(await screen.findByTestId('overview-queue')).toBeInTheDocument()
    expect(screen.queryByText('Очередь пуста')).not.toBeInTheDocument()
    expect(screen.getByText('Нет задач в обзоре')).toBeInTheDocument()
    expect(apiClient.trackerIssues).not.toHaveBeenCalled()
  })

  it('does not retain cached Tracker issues after a generic role loses tracker.read', async () => {
    const user = makeUser({ role: 'field_lead', permissions: ['nav.dashboard', 'nav.tasks'] })
    const cached: OverviewPayload = {
      kind: 'park',
      park,
      summary: { ...summary, moving: [] },
      issues: [{ key: 'PRIVATE-42', summary: 'Задача из прежнего доступа', status: 'Open', url: 'https://tracker.example/PRIVATE-42' }],
    }
    resourceStore.set(cacheKey(user), cached, true)
    const apiClient = client({ dashboardSummary: vi.fn(async () => { throw new TypeError('offline') }) })
    renderOverview({ user, apiClient })

    expect(await screen.findByText('Нет сети')).toBeInTheDocument()
    expect(screen.getByTestId('overview-scope')).toBeInTheDocument()
    expect(screen.getByTestId('overview-metrics')).toHaveTextContent('В очереди')
    expect(screen.queryAllByText(/Задача из прежнего доступа/)).toHaveLength(0)
    expect(screen.queryAllByRole('link', { name: /PRIVATE-42/ })).toHaveLength(0)
    expect(screen.queryByTestId('overview-risk')).not.toBeInTheDocument()
    expect(apiClient.trackerIssues).not.toHaveBeenCalled()
  })

  it('uses current park readiness before projecting cached queue data', async () => {
    const unconfiguredPark = { ...park, tracker_queue: '' }
    const user = makeUser({ role: 'field_lead', parks: [unconfiguredPark] })
    resourceStore.set(cacheKey(user), payload(), true)
    renderOverview({
      user,
      apiClient: client({ dashboardSummary: vi.fn(async () => { throw new TypeError('offline') }) }),
    })

    expect(await screen.findByText('Нет сети')).toBeInTheDocument()
    expect(screen.getByText('Парк не готов к работе с Tracker')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /ROBOPARK-42/ })).not.toBeInTheDocument()
    expect(screen.queryByTestId('overview-action')).not.toBeInTheDocument()
  })

  it.each(['removed', 'added'] as const)('does not retain a fleet aggregate when an accessible park was %s', async (change) => {
    const otherPark = { ...park, id: 8, name: 'Юг', tag: 'Beta' }
    const currentParks = change === 'removed' ? [park] : [park, otherPark]
    const cachedParks = change === 'removed' ? [park, otherPark] : [park]
    const user = makeUser({ role: 'royal', parks: currentParks })
    vi.spyOn(api, 'parks').mockResolvedValue(currentParks)
    resourceStore.set(cacheKey(user), {
      kind: 'fleet',
      summaries: cachedParks.map((cachedPark) => ({
        park: cachedPark,
        summary: {
          ...summary,
          park_id: cachedPark.id,
          moving: [{ key: `CACHED-${cachedPark.id}`, summary: 'Задача из старой области' }],
        },
      })),
    } satisfies OverviewPayload, true)
    const commits: string[] = []
    render(overviewTree({
      user,
      apiClient: client({ dashboardSummary: vi.fn(async () => { throw new TypeError('offline') }) }),
      onRender: () => { commits.push(document.body.textContent ?? '') },
    }))

    expect(await screen.findByText('Нет сети')).toBeInTheDocument()
    expect(screen.queryByTestId('overview-scope')).not.toBeInTheDocument()
    expect(screen.queryByTestId('overview-metrics')).not.toBeInTheDocument()
    expect(screen.queryAllByRole('link', { name: /CACHED-/ })).toHaveLength(0)
    expect(commits.every((text) => !text.includes('Задача из старой области'))).toBe(true)
  })

  it('retains a matching fleet cache with current park names and distinct scoped task links', async () => {
    const otherPark = { ...park, id: 8, name: 'Юг', tag: 'Beta' }
    const user = makeUser({ role: 'royal', parks: [park, otherPark] })
    vi.spyOn(api, 'parks').mockResolvedValue([{ ...park, name: 'Северный парк' }, otherPark])
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    resourceStore.set(cacheKey(user), {
      kind: 'fleet',
      summaries: user.parks.map((item) => ({ park: item, summary: { ...summary, park_id: item.id } })),
    } satisfies OverviewPayload, true)
    renderOverview({
      user,
      apiClient: client({ dashboardSummary: vi.fn(async () => { throw new TypeError('offline') }) }),
    })

    expect(await screen.findByText('Нет сети')).toBeInTheDocument()
    expect(screen.getByTestId('overview-scope')).toHaveTextContent('Все доступные активные парки · 2')
    expect(screen.getByRole('link', { name: 'Открыть работу парка Северный парк' })).toHaveAttribute('href', '/work?park=7')
    expect(screen.getAllByRole('link', { name: /^Открыть задачу ROBOPARK-42:/ }).map((link) => link.getAttribute('href'))).toEqual([
      '/work/ROBOPARK-42?park=7&queue=ROBOPARK',
      '/work/ROBOPARK-42?park=8&queue=ROBOPARK',
    ])
    expect(consoleError).not.toHaveBeenCalled()
  })

  it.each([
    ['field_lead', ['nav.dashboard', 'nav.admin'], true],
    ['admin', ['nav.dashboard'], false],
  ] as const)('uses manifest capability instead of the %s role for missing-configuration administration access', async (role, permissions, allowed) => {
    const unconfiguredPark = { ...park, tracker_queue: '' }
    vi.spyOn(api, 'parks').mockResolvedValue([unconfiguredPark])
    renderOverview({
      user: makeUser({ role, permissions: [...permissions], parks: [unconfiguredPark] }),
      apiClient: client({ dashboardSummary: vi.fn(async () => ({ ...summary, moving: [] })) }),
    })

    expect(await screen.findByText('Парк не готов к работе с Tracker')).toBeInTheDocument()
    const adminLink = document.querySelector('a[href="/admin"]')
    expect(Boolean(adminLink)).toBe(allowed)
    if (!allowed) {
      expect(screen.queryByTestId('overview-action')).not.toBeInTheDocument()
      expect(screen.getByTestId('overview-risk')).toHaveTextContent(/администратор/i)
    }
  })

  it('does not paint the previous user overview while the next user request is pending', async () => {
    const firstUser = makeUser()
    const commits: string[] = []
    const onRender = () => { commits.push(document.body.textContent ?? '') }
    const view = render(overviewTree({ user: firstUser, apiClient: client(), onRender }))
    await screen.findByRole('link', { name: 'Разобрать ROBOPARK-42' })

    const pending = deferred<DashboardSummary>()
    const nextUser = makeUser({ id: 4, username: 'operator-2' })
    const nextClient = client({ dashboardSummary: vi.fn(() => pending.promise) })
    const beforeSwitch = commits.length
    view.rerender(overviewTree({ user: nextUser, apiClient: nextClient, onRender }))

    expect(commits.slice(beforeSwitch)).not.toEqual([])
    expect(commits.slice(beforeSwitch).every((text) => !text.includes('ROBOPARK-42'))).toBe(true)
    expect(screen.queryByRole('link', { name: /ROBOPARK-42/ })).not.toBeInTheDocument()
    view.unmount()
    await act(async () => { pending.resolve(summary) })
  })
})
