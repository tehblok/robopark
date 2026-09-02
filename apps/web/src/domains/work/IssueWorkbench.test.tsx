import { Profiler, type ReactNode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  type Park,
  type Paged,
  type TrackerIssue,
  type TrackerIssueDetail,
  type User,
} from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { ru } from '../../i18n/ru'
import { resetCoalescingForTests, resourceStore } from '../../lib/resource'
import { IssueWorkbench, type IssueWorkbenchApiClient } from './IssueWorkbench'
import { WorkPage } from './WorkPage'
import type { WorkUrlState } from './workUrl'

const state: WorkUrlState = {
  filters: { queue: 'ROBOPARK' },
  sort: 'oldest',
  page: 1,
}

const park: Park = {
  id: 7,
  name: 'Север',
  tag: 'Alpha',
  tracker_queue: 'ROBOPARK',
}

const user: User = {
  id: 3,
  username: 'operator',
  role: 'operator',
  access_status: 'approved',
  permissions: ['tracker.read', 'tracker.write'],
  tracker_login: 'operator',
  parks: [park],
}

const capabilities = {
  comment: true,
  assign: true,
  unassign: true,
  transition: true,
  close: true,
  attach: true,
}

const issue: TrackerIssueDetail = {
  key: 'ROBOPARK-42',
  summary: 'Робот не продолжает маршрут',
  status: 'Open',
  queue: 'ROBOPARK',
  robot: '447',
  url: 'https://st.yandex-team.ru/ROBOPARK-42',
  capabilities,
}

function page(items: TrackerIssue[] = [issue]): Paged<TrackerIssue> {
  return {
    items,
    total: items.length ? 51 : 0,
    limit: 50,
    offset: 0,
    has_more: items.length > 0,
  }
}

function actionResult(action: string) {
  return {
    key: issue.key,
    action,
    status: issue.status,
    actor: user.username,
    performed_at: '2026-09-02T09:00:00Z',
  }
}

function apiClient(
  overrides: Partial<IssueWorkbenchApiClient> = {},
): IssueWorkbenchApiClient {
  return {
    trackerIssues: vi.fn(async () => page()),
    trackerIssue: vi.fn(async () => issue),
    trackerComments: vi.fn(async () => []),
    trackerTransitions: vi.fn(async () => [{ id: 'resolve', display: 'Решить' }]),
    trackerComment: vi.fn(async () => actionResult('comment')),
    trackerAttach: vi.fn(async () => actionResult('attach')),
    trackerAssign: vi.fn(async () => actionResult('assign')),
    trackerUnassign: vi.fn(async () => actionResult('unassign')),
    trackerTransition: vi.fn(async () => actionResult('transition')),
    trackerClose: vi.fn(async () => actionResult('close')),
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

function Harness({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>
}

function renderWorkbench({
  client = apiClient(),
  selectedIssue = issue.key,
  currentState = state,
  currentUser = user,
  selectedPark = park,
  onAuthorizationFailure = vi.fn(async () => undefined),
}: {
  client?: IssueWorkbenchApiClient
  selectedIssue?: string
  currentState?: WorkUrlState
  currentUser?: User
  selectedPark?: Park
  onAuthorizationFailure?: () => Promise<unknown>
} = {}) {
  const onStateChange = vi.fn()
  const onOpenIssue = vi.fn()
  const onCloseIssue = vi.fn()
  const view = render(
    <IssueWorkbench
      apiClient={client}
      issueKey={selectedIssue}
      onAuthorizationFailure={onAuthorizationFailure}
      onCloseIssue={onCloseIssue}
      onOpenIssue={onOpenIssue}
      onStateChange={onStateChange}
      selectedPark={selectedPark}
      state={currentState}
      user={currentUser}
    />,
    { wrapper: Harness },
  )
  return {
    ...view,
    onAuthorizationFailure,
    onCloseIssue,
    onOpenIssue,
    onStateChange,
  }
}

function listKey(currentUser = user, currentPark = park, currentState = state) {
  return `work:${currentUser.id}:list:${currentPark.id}:${JSON.stringify(currentState)}`
}

function seedCurrentWork(currentUser = user, currentIssue = issue) {
  resourceStore.set(listKey(currentUser), page([currentIssue]), true)
  resourceStore.set(`work:${currentUser.id}:issue:${currentIssue.key}`, currentIssue, true)
  resourceStore.set(`work:${currentUser.id}:comments:${currentIssue.key}`, [], true)
  resourceStore.set(
    `work:${currentUser.id}:transitions:${currentIssue.key}`,
    [{ id: 'resolve', display: 'Решить' }],
    true,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
  resourceStore.clearAll()
  resetCoalescingForTests()
  window.history.replaceState({}, '', '/')
})

describe('IssueWorkbench', () => {
  it('loads the URL-selected issue, exposes its robot and paginates through URL state', async () => {
    const client = apiClient()
    const { onStateChange } = renderWorkbench({ client })

    expect(
      await screen.findByRole('heading', { name: issue.summary }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Проверить робота 447' }),
    ).toHaveAttribute('href', '/robots/447/check')

    fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
    expect(onStateChange).toHaveBeenCalledWith(
      { filters: { queue: 'ROBOPARK' }, sort: 'oldest', page: 2 },
      { replace: false },
    )
    expect(client.trackerIssue).toHaveBeenCalledWith(issue.key)
  })

  it('never loads transitions when the selected issue capability denies them', async () => {
    const readOnlyIssue = {
      ...issue,
      capabilities: { ...capabilities, transition: false },
    }
    const trackerTransitions = vi.fn(async () => [])
    const client = apiClient({
      trackerIssue: vi.fn(async () => readOnlyIssue),
      trackerTransitions,
    })

    renderWorkbench({ client })

    await screen.findByRole('heading', { name: issue.summary })
    await waitFor(() => expect(client.trackerIssue).toHaveBeenCalledOnce())
    expect(trackerTransitions).not.toHaveBeenCalled()
  })

  it('invalidates and refreshes only the selected issue resources after mutation', async () => {
    const invalidate = vi.spyOn(resourceStore, 'invalidate')
    const clearAll = vi.spyOn(resourceStore, 'clearAll')
    const client = apiClient()
    renderWorkbench({ client })

    await screen.findByRole('heading', { name: issue.summary })
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), {
      target: { value: 'Новая деталь' },
    })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))

    await screen.findByText('Действие выполнено')
    expect(invalidate).toHaveBeenCalledTimes(4)
    expect(invalidate).toHaveBeenCalledWith(`work:${user.id}:list:${park.id}:`, {
      prefix: true,
    })
    expect(invalidate).toHaveBeenCalledWith(`work:${user.id}:issue:${issue.key}`)
    expect(invalidate).toHaveBeenCalledWith(`work:${user.id}:comments:${issue.key}`)
    expect(invalidate).toHaveBeenCalledWith(`work:${user.id}:transitions:${issue.key}`)
    expect(clearAll).not.toHaveBeenCalled()
  })

  it('keeps cached protected work visible only for a transient revalidation failure', async () => {
    seedCurrentWork()
    const transient = new TypeError('offline')
    const client = apiClient({
      trackerIssues: vi.fn(async () => { throw transient }),
      trackerIssue: vi.fn(async () => { throw transient }),
      trackerComments: vi.fn(async () => { throw transient }),
      trackerTransitions: vi.fn(async () => { throw transient }),
    })

    renderWorkbench({ client })

    expect(
      await screen.findByRole('heading', { name: issue.summary }),
    ).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getAllByRole('alert').length).toBeGreaterThan(0)
    })
    expect(screen.getAllByText(/Нет связи с источником/).length).toBeGreaterThan(0)
    expect(screen.getByLabelText(ru.tracker.comments)).toBeInTheDocument()
  })

  it('gives a non-retainable detail-side failure priority over transient stale data', async () => {
    seedCurrentWork()
    const onAuthorizationFailure = vi.fn(async () => undefined)
    const client = apiClient({
      trackerIssue: vi.fn(async () => { throw new TypeError('offline') }),
      trackerComments: vi.fn(async () => {
        throw new ApiError(403, 'tracker_token_not_configured', 'req-comments')
      }),
      trackerTransitions: vi.fn(async () => { throw new TypeError('offline') }),
    })

    renderWorkbench({ client, onAuthorizationFailure })

    expect(
      await screen.findByRole('heading', { name: 'Требуется настройка' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: issue.summary })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(ru.tracker.comments)).not.toBeInTheDocument()
    expect(screen.getByText('Код запроса: req-comments')).toBeInTheDocument()
    expect(onAuthorizationFailure).not.toHaveBeenCalled()
  })

  it('renders a full local error instead of protected cache for configuration failures', async () => {
    seedCurrentWork()
    const onAuthorizationFailure = vi.fn(async () => undefined)
    const client = apiClient({
      trackerIssues: vi.fn(async () => {
        throw new ApiError(403, 'tracker_token_not_configured', 'req-config')
      }),
    })

    renderWorkbench({ client, onAuthorizationFailure, selectedIssue: '' })

    expect(
      await screen.findByRole('heading', { name: 'Требуется настройка' }),
    ).toBeInTheDocument()
    expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()
    expect(screen.getByText('Код запроса: req-config')).toBeInTheDocument()
    expect(onAuthorizationFailure).not.toHaveBeenCalled()
  })

  it.each([401, 403])(
    'suppresses and evicts only the denied user after HTTP %s',
    async (status) => {
      seedCurrentWork()
      resourceStore.set('work:30:list:7:other', { secret: 'other-user' }, true)
      resourceStore.set('overview:3', { secret: 'other-domain' }, true)
      const refreshGate = deferred<unknown>()
      const onAuthorizationFailure = vi.fn(() => refreshGate.promise)
      const denial = new ApiError(status, status === 401 ? 'session_expired' : 'forbidden')
      const client = apiClient({
        trackerIssues: vi.fn(async () => { throw denial }),
        trackerIssue: vi.fn(async () => { throw denial }),
      })

      renderWorkbench({ client, onAuthorizationFailure })

      expect(
        await screen.findByRole('heading', {
          name: status === 401 ? 'Сессия истекла' : 'Нет доступа',
        }),
      ).toBeInTheDocument()
      expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()
      expect(screen.queryByLabelText(ru.tracker.comments)).not.toBeInTheDocument()
      expect(onAuthorizationFailure).toHaveBeenCalledTimes(1)
      expect(resourceStore.get(listKey())).toBeUndefined()
      expect(resourceStore.get(`work:${user.id}:issue:${issue.key}`)).toBeUndefined()
      expect(resourceStore.get('work:30:list:7:other')).toEqual({ secret: 'other-user' })
      expect(resourceStore.get('overview:3')).toEqual({ secret: 'other-domain' })

      refreshGate.resolve(undefined)
    },
  )

  it('routes mutation authorization failures through the same fail-closed boundary', async () => {
    const onAuthorizationFailure = vi.fn(async () => undefined)
    const client = apiClient({
      trackerComment: vi.fn(async () => {
        throw new ApiError(401, 'session_expired')
      }),
    })
    renderWorkbench({ client, onAuthorizationFailure })

    await screen.findByRole('heading', { name: issue.summary })
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), {
      target: { value: 'Проверьте маршрут' },
    })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))

    expect(
      await screen.findByRole('heading', { name: 'Сессия истекла' }),
    ).toBeInTheDocument()
    expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()
    expect(onAuthorizationFailure).toHaveBeenCalledTimes(1)
  })

  it('does not publish a sibling load that resolves after authorization eviction', async () => {
    const pendingList = deferred<Paged<TrackerIssue>>()
    const client = apiClient({
      trackerIssues: vi.fn(() => pendingList.promise),
      trackerIssue: vi.fn(async () => {
        throw new ApiError(403, 'forbidden')
      }),
    })
    const view = renderWorkbench({ client })

    await screen.findByRole('heading', { name: 'Нет доступа' })
    view.unmount()

    await act(async () => {
      pendingList.resolve(page())
      await pendingList.promise
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(resourceStore.get(listKey())).toBeUndefined()
    expect(window.localStorage.getItem(`robopark:res:${listKey()}`)).toBeNull()
  })

  it('never paints a previous user resource after the owner identity changes', async () => {
    seedCurrentWork()
    const pendingList = deferred<Paged<TrackerIssue>>()
    const pendingDetail = deferred<TrackerIssueDetail>()
    const client = apiClient({
      trackerIssues: vi.fn(() => pendingList.promise),
      trackerIssue: vi.fn(() => pendingDetail.promise),
      trackerComments: vi.fn(() => new Promise<never>(() => undefined)),
      trackerTransitions: vi.fn(() => new Promise<never>(() => undefined)),
    })
    const commits: string[] = []
    const renderTree = (currentUser: User) => (
      <Harness>
        <Profiler
          id="workbench"
          onRender={() => commits.push(document.body.textContent ?? '')}
        >
          <IssueWorkbench
            apiClient={client}
            issueKey={issue.key}
            onAuthorizationFailure={vi.fn(async () => undefined)}
            onCloseIssue={vi.fn()}
            onOpenIssue={vi.fn()}
            onStateChange={vi.fn()}
            selectedPark={park}
            state={state}
            user={currentUser}
          />
        </Profiler>
      </Harness>
    )
    const view = render(renderTree(user))
    await screen.findByRole('heading', { name: issue.summary })
    const beforeSwitch = commits.length

    const nextUser = { ...user, id: 4, username: 'operator-2' }
    view.rerender(renderTree(nextUser))

    expect(commits.slice(beforeSwitch)).not.toEqual([])
    expect(commits.slice(beforeSwitch).every((text) => !text.includes(issue.summary))).toBe(true)
    expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()

    view.unmount()
    pendingList.resolve(page())
    pendingDetail.resolve(issue)
  })

  it('does not expose the untagged filter to a capable custom role', async () => {
    const customUser = {
      ...user,
      role: 'dispatcher',
      permissions: ['tracker.read', 'tracker.write', 'nav.tasks'],
    }
    renderWorkbench({ currentUser: customUser, selectedIssue: '' })

    await waitFor(() => expect(screen.queryByText(ru.loading)).not.toBeInTheDocument())
    expect(screen.queryByRole('checkbox', { name: 'Без тега парка' })).not.toBeInTheDocument()
  })
})

describe('WorkPage cold states', () => {
  function renderPage({
    loading = false,
    selectedPark = park as Park | null,
    parkId = selectedPark?.id ?? null,
    client = apiClient(),
  }: {
    loading?: boolean
    selectedPark?: Park | null
    parkId?: number | null
    client?: IssueWorkbenchApiClient
  } = {}) {
    return render(
      <AuthContext.Provider
        value={{
          loading: false,
          login: vi.fn(async () => user),
          logout: vi.fn(async () => undefined),
          refreshUser: vi.fn(async () => user),
          user,
        }}
      >
        <ParkScopeContext.Provider
          value={{
            loading,
            locked: false,
            parkId,
            parks: selectedPark ? [selectedPark] : [],
            refreshParks: vi.fn(async () => undefined),
            selectedPark,
            setParkId: vi.fn(),
          }}
        >
          <MemoryRouter initialEntries={['/work?park=7']}>
            <Routes>
              <Route element={<WorkPage apiClient={client} />} path="/work" />
            </Routes>
          </MemoryRouter>
        </ParkScopeContext.Provider>
      </AuthContext.Provider>,
    )
  }

  it('does not query Tracker while park scope is loading', () => {
    const client = apiClient()
    renderPage({ client, loading: true })

    expect(screen.getByLabelText('Загружаем область работы')).toBeInTheDocument()
    expect(client.trackerIssues).not.toHaveBeenCalled()
  })

  it('does not query Tracker without a selected park', () => {
    const client = apiClient()
    renderPage({ client, parkId: null, selectedPark: null })

    expect(screen.getByRole('heading', { name: 'Парк не выбран' })).toBeInTheDocument()
    expect(client.trackerIssues).not.toHaveBeenCalled()
  })

  it('does not query Tracker when the selected park has no queue', () => {
    const client = apiClient()
    renderPage({
      client,
      selectedPark: { ...park, tracker_queue: ' ' },
    })

    expect(screen.getByRole('heading', { name: 'Очередь не настроена' })).toBeInTheDocument()
    expect(client.trackerIssues).not.toHaveBeenCalled()
  })
})
