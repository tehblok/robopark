import { webcrypto } from 'node:crypto'
import { Profiler, type ReactNode, useLayoutEffect, useState } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import {
  api,
  ApiError,
  type InventoryCatalogSearchItem,
  type Park,
  type Paged,
  type TrackerIssue,
  type TrackerIssueDetail,
  type User,
} from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { ParkScopeProvider } from '../../app/park/ParkScopeProvider'
import { ru } from '../../i18n/ru'
import { collaborationClient } from '../../components/tracker/collaborationClient'
import { resetCoalescingForTests, resourceStore } from '../../lib/resource'
import { IssueWorkbench, type IssueWorkbenchApiClient } from './IssueWorkbench'
import { WorkPage } from './WorkPage'
import {
  buildWorkSearch,
  readWorkScroll,
  saveWorkScroll,
  type WorkUrlState,
} from './workUrl'

type WorkPageApiClient = IssueWorkbenchApiClient & Pick<typeof api, 'dashboardSummary'>

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
): WorkPageApiClient {
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
    inventory: vi.fn(async parkId => ({ park_id: parkId, component_count: 0, part_count: 0, low_stock_count: 0, out_of_stock_count: 0, components: [] })),
    searchInventory: vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 })),
    writeoffInventoryForTask: vi.fn(),
    inventoryComponentPhotoUrl: vi.fn(id => `/api/inventory/components/${id}/photo`),
    inventoryPartPhotoUrl: vi.fn(id => `/api/inventory/parts/${id}/photo`),
    dashboardSummary: vi.fn(async parkId => ({
      park_id: parkId, generated_at: '2026-09-15T09:00:00Z', arrived: 0, done: 0,
      queued: 0, in_transit: 0, moving: [],
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

it('requires a mechanic to claim a task before opening it', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: 'mech.login' }
  const unassigned = { ...issue, assignee: null }
  const client = apiClient({ trackerIssues: vi.fn(async () => page([unassigned])) })
  renderWorkbench({ client, selectedIssue: '', currentUser: mechanic })

  const take = await screen.findByRole('button', { name: 'Взять в работу' })
  expect(screen.queryByRole('button', { name: /Открыть задачу/ })).not.toBeInTheDocument()
  fireEvent.click(take)
  await waitFor(() => expect(client.trackerAssign).toHaveBeenCalledWith(issue.key, 'mech1'))
})

it('shows a claimed mechanic task only once', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owned = { ...issue, assignee: { display: 'mech1', login: 'mech1' } }
  const client = apiClient({ trackerIssues: vi.fn(async () => page([owned])) })

  renderWorkbench({ client, selectedIssue: '', currentUser: mechanic })

  expect(await screen.findAllByRole('button', {
    name: `Открыть задачу ${issue.key}: ${issue.summary}`,
  })).toHaveLength(1)
})

it('pins owned active tasks from an independent query and deduplicates the queue', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owned = { ...issue, key: 'ROBOPARK-OWNED', summary: 'Моя активная', assignee: { display: 'mech1', login: 'mech1' } }
  const queued = { ...issue, key: 'ROBOPARK-QUEUE', summary: 'Общая очередь' }
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) => (
    query.owned_by_me
      ? page([owned])
      : page([queued, owned])
  ))
  renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '',
    currentState: { filters: { queue: 'ROBOPARK', status: 'queued' }, sort: 'oldest', page: 1 },
  })

  expect(await screen.findByRole('heading', { name: 'Мои задачи в работе' })).toBeVisible()
  const rows = screen.getAllByRole('article')
  expect(rows.map(row => row.textContent)).toEqual([
    expect.stringContaining('ROBOPARK-OWNED'),
    expect.stringContaining('ROBOPARK-QUEUE'),
  ])
  expect(trackerIssues).toHaveBeenCalledWith(expect.objectContaining({
    owned_by_me: true, open_only: true, sort: 'oldest', limit: 50, offset: 0,
  }))
  const ownedQuery = trackerIssues.mock.calls.find(([query]) => query.owned_by_me)?.[0]
  expect(ownedQuery).not.toHaveProperty('assignee')
  expect(ownedQuery).not.toHaveProperty('queue')
  expect(ownedQuery).not.toHaveProperty('park')
  expect(ownedQuery).not.toHaveProperty('status')
})

it('displays and writes task parts from the backend claim park despite tag and user-park order', async () => {
  const claimPark = { ...park, id: 7, name: 'A', tag: 'Alpha' }
  const otherPark = { ...park, id: 8, name: 'B', tag: 'Beta' }
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [otherPark, claimPark] }
  const claimedIssue = {
    ...issue,
    tags: ['Beta', 'Alpha'],
    claim: { park_id: claimPark.id },
    assignee: { display: 'mech', login: 'mech' },
  }
  const part: InventoryCatalogSearchItem = {
    id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91',
    is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1',
    location: 'Склад парка A', stock_is_active: true,
  }
  const searchInventory = vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 }))
  const writeoffInventoryForTask = vi.fn()
  const client = apiClient({ trackerIssue: vi.fn(async () => claimedIssue), searchInventory, writeoffInventoryForTask })

  renderWorkbench({ client, currentUser: mechanic, selectedPark: otherPark })
  fireEvent.click(await screen.findByRole('button', { name: 'Использовать запчасть' }))

  expect(await screen.findByRole('option', { name: 'Колёса' })).toBeInTheDocument()
  fireEvent.change(screen.getByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  expect(screen.getByText('Склад парка A')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  expect(searchInventory).toHaveBeenCalledWith({ parkId: 7, stockFilter: 'in_stock', limit: 200, offset: 0 })
  expect(searchInventory).not.toHaveBeenCalledWith(expect.objectContaining({ parkId: 8 }))
  await waitFor(() => expect(writeoffInventoryForTask).toHaveBeenCalledWith(issue.key, -91, '1', expect.any(String)))
})

it('disables task parts when the backend claim is missing', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const claimedIssue = { ...issue, tags: ['Alpha'], claim: null, assignee: { display: 'mech', login: 'mech' } }
  const searchInventory = vi.fn(async () => ({ items: [], limit: 200, offset: 0, total: 0 }))
  const client = apiClient({ trackerIssue: vi.fn(async () => claimedIssue), searchInventory })

  renderWorkbench({ client, currentUser: mechanic, selectedPark: park })
  fireEvent.click(await screen.findByRole('button', { name: 'Использовать запчасть' }))

  expect(screen.getByText('Парк задачи недоступен')).toBeVisible()
  expect(searchInventory).not.toHaveBeenCalled()
})

it('lets a mechanic inspect and explicitly take over a shiftmates task', async () => {
  const mechanic: User = { ...user, username: 'mech2', role: 'mechanic', tracker_login: null }
  const claimedByShiftmate = { ...issue, assignee: { display: 'Сменщик', login: 'mech1' } }
  const client = apiClient({ trackerIssues: vi.fn(async () => page([claimedByShiftmate])) })
  renderWorkbench({ client, selectedIssue: '', currentUser: mechanic })

  expect(await screen.findByRole('button', {
    name: `Открыть задачу ${issue.key}: ${issue.summary}`,
  })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Взять вместо сменщика' }))
  await waitFor(() => expect(client.trackerAssign).toHaveBeenCalledWith(issue.key, 'mech2'))
})

it('renders a duplicate upstream task key only once', async () => {
  const client = apiClient({ trackerIssues: vi.fn(async () => page([issue, issue])) })
  renderWorkbench({ client, selectedIssue: '' })
  expect(await screen.findAllByRole('button', {
    name: `Открыть задачу ${issue.key}: ${issue.summary}`,
  })).toHaveLength(1)
})

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
  strictMode = false,
}: {
  client?: IssueWorkbenchApiClient
  selectedIssue?: string
  currentState?: WorkUrlState
  currentUser?: User
  selectedPark?: Park
  onAuthorizationFailure?: () => Promise<unknown>
  strictMode?: boolean
} = {}) {
  const onStateChange = vi.fn()
  const onOpenIssue = vi.fn()
  const onCloseIssue = vi.fn()
  function ControlledWorkbench() {
    const [value, setValue] = useState(currentState)
    return <IssueWorkbench apiClient={client} issueKey={selectedIssue}
      onAuthorizationFailure={onAuthorizationFailure} onCloseIssue={onCloseIssue} onOpenIssue={onOpenIssue}
      onStateChange={(next, options) => { onStateChange(next, options); setValue(next) }}
      selectedPark={selectedPark} state={value} user={currentUser} />
  }
  const view = render(<ControlledWorkbench />, { wrapper: Harness, reactStrictMode: strictMode })
  return {
    ...view,
    onAuthorizationFailure,
    onCloseIssue,
    onOpenIssue,
    onStateChange,
  }
}

function listKey(currentUser = user, currentPark = park, currentState = state) {
  return `${accessPrefix(currentUser, currentPark)}list:${currentPark.id}:${JSON.stringify(currentState)}`
}

// Cache fixture serialization only; regression expectations use rendered data,
// action availability and external effects, not this key builder.
function accessPrefix(currentUser = user, currentPark = park) {
  const scope = (item: Park) => [item.id, item.tag?.trim(), item.tracker_queue?.trim(), item.is_active !== false]
  return `work:${currentUser.id}:${JSON.stringify([
    currentUser.id, currentUser.username, currentUser.tracker_login, currentUser.role, currentUser.access_status,
    Boolean(currentUser.must_change_password), [...new Set(currentUser.permissions ?? [])].sort(),
    [...currentUser.parks].sort((a, b) => a.id - b.id).map(scope), scope(currentPark),
  ])}:`
}

function seedCurrentWork(currentUser = user, currentIssue = issue) {
  // Seed stale data so these revalidation tests exercise a real refresh.
  const clock = vi.spyOn(Date, 'now').mockReturnValue(Date.now() - 60_000)
  resourceStore.set(listKey(currentUser), page([currentIssue]), true)
  resourceStore.set(`${accessPrefix(currentUser)}issue:${currentIssue.key}`, currentIssue, true)
  resourceStore.set(`${accessPrefix(currentUser)}comments:${currentIssue.key}`, [], true)
  resourceStore.set(
    `${accessPrefix(currentUser)}transitions:${currentIssue.key}`,
    [{ id: 'resolve', display: 'Решить' }],
    true,
  )
  clock.mockRestore()
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  resourceStore.clearAll()
  resetCoalescingForTests()
  window.history.replaceState({}, '', '/')
})

beforeEach(() => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
})

describe('IssueWorkbench', () => {
  it('uses workflow owner and server comment eligibility as authoritative state', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = {
      ...issue, assignee: { display: 'stale', login: 'stale' }, claim: { park_id: park.id },
      workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: true },
    }
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => workflowIssue) }) })
    expect(await screen.findByRole('button', { name: 'Передать на проверку' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Передать на проверку' }))
    expect(await screen.findByRole('textbox', { name: 'Добавить уточнение' })).not.toBeRequired()
  })

  it('retries a locally committed message with the same idempotency key', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = { ...issue, claim: { park_id: park.id }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'pending', has_current_cycle_comment: false } }
    const taskMessage = vi.fn().mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({})
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => workflowIssue), taskMessage }) })
    const composer = await screen.findByRole('textbox', { name: ru.tracker.comments })
    fireEvent.change(composer, { target: { value: 'Заменил датчик' } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await waitFor(() => expect(taskMessage).toHaveBeenCalledTimes(2))
    expect(taskMessage.mock.calls[0]?.[2]).toBe(taskMessage.mock.calls[1]?.[2])
  })

  it('uses lifecycle handoff and keeps its key when the response is lost', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = { ...issue, claim: { park_id: park.id }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: true } }
    const taskHandoff = vi.fn().mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({})
    const client = apiClient({ trackerIssue: vi.fn(async () => workflowIssue), taskHandoff })
    vi.spyOn(api, 'trackerUsers').mockResolvedValue([])
    renderWorkbench({ currentUser: mechanic, client })
    fireEvent.click(await screen.findByRole('button', { name: 'Передать смену' }))
    fireEvent.change(screen.getByLabelText('Логин сменщика'), { target: { value: 'bob' } })
    fireEvent.change(screen.getByLabelText('Причина передачи'), { target: { value: 'Конец смены' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Передать смену' }).at(-1)!)
    await screen.findByRole('alert')
    fireEvent.click(screen.getAllByRole('button', { name: 'Передать смену' }).at(-1)!)
    await waitFor(() => expect(taskHandoff).toHaveBeenCalledTimes(2))
    expect(taskHandoff.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ assignee: 'bob', reason: 'Конец смены' }))
    expect(taskHandoff.mock.calls[0]?.[2]).toBe(taskHandoff.mock.calls[1]?.[2])
    await waitFor(() => expect(vi.mocked(client.trackerIssues).mock.calls.filter(
      ([query]) => query.owned_by_me,
    )).toHaveLength(2))
    await act(async () => { await Promise.resolve() })
    expect(vi.mocked(client.trackerIssues).mock.calls.filter(
      ([query]) => query.owned_by_me,
    )).toHaveLength(2)
  })

  it('maps workflow status once and keeps unknown values nontechnical', async () => {
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async (): Promise<TrackerIssueDetail> => ({ ...issue, workflow: { owner: null, review_state: null, display_status: 'future' as never, sync_state: 'saved', has_current_cycle_comment: false } })) }) })
    expect(await screen.findByText('Статус обновляется')).toBeVisible()
    expect(screen.getAllByText('Статус обновляется')).toHaveLength(1)
    expect(screen.queryByText('future')).not.toBeInTheDocument()
  })
  it('uses one chat and keeps task actions in compact lifecycle order', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => ({
      ...issue, assignee: { display: 'mech', login: 'mech' }, claim: { park_id: park.id },
      workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false },
    })) }) })
    expect(await screen.findByRole('heading', { name: issue.summary })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'История действий' })).not.toBeInTheDocument()
    const parts = screen.getByRole('button', { name: 'Заказать запчасть' })
    const handoff = screen.getByRole('button', { name: 'Передать смену' })
    expect(parts).toHaveAttribute('aria-expanded', 'false')
    expect(handoff).toHaveAttribute('aria-expanded', 'false')
    expect(parts.compareDocumentPosition(handoff) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Статус задачи' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: ru.tracker.actions.openInTracker })).not.toBeInTheDocument()
  })
  it('shows the write-off control in the main tab only to the mechanic assigned to the task', async () => {
    const mechanic: User = { ...user, role: 'mechanic', username: 'mech', tracker_login: 'Mech.Login' }
    const owned = { ...issue, assignee: { display: 'Mechanic', login: 'mech' } }
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => owned) }), currentUser: mechanic })

    expect(await screen.findByText('Использовать запчасть')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Задача' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: 'Запчасти' })).not.toBeInTheDocument()
  })

  it.each([
    { label: 'unassigned task', currentUser: { ...user, role: 'mechanic' as const, username: 'mech', tracker_login: 'mech.login' }, currentIssue: { ...issue, assignee: null } },
    { label: 'foreign-assigned task', currentUser: { ...user, role: 'mechanic' as const, username: 'mech', tracker_login: 'mech.login' }, currentIssue: { ...issue, assignee: { display: 'Other', login: 'other' } } },
    { label: 'operator', currentUser: user, currentIssue: { ...issue, assignee: { display: 'Mechanic', login: 'mech.login' } } },
  ])('does not show the write-off control for $label', async ({ currentUser, currentIssue }) => {
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => currentIssue) }), currentUser })

    await screen.findByRole('heading', { name: currentIssue.summary })
    expect(screen.queryByText('Использовать запчасть')).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Запчасти' })).not.toBeInTheDocument()
  })

  it('keeps a shiftmates task readable but hides mutations and robot diagnostics until takeover', async () => {
    const mechanic: User = { ...user, role: 'mechanic', username: 'mech', tracker_login: null }
    const claimedByShiftmate = { ...issue, assignee: { display: 'Сменщик', login: 'other' } }
    const { onStateChange } = renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => claimedByShiftmate) }), currentUser: mechanic })

    expect(await screen.findByRole('heading', { name: claimedByShiftmate.summary })).toBeInTheDocument()
    expect(screen.getByText(/Для изменений возьмите задачу вместо сменщика/)).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Проверка робота' })).not.toBeInTheDocument()
    const robotField = screen.getByText(ru.tracker.fields.robot, { selector: 'dt' }).parentElement
    expect(robotField).toHaveTextContent('447')
    expect(within(robotField!).queryByRole('button')).not.toBeInTheDocument()
    expect(within(robotField!).queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Проверить робота 447' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: ru.tracker.actions.close })).not.toBeInTheDocument()
    expect(onStateChange).not.toHaveBeenCalled()
  })

  it('does not start presence polling for a shiftmates task', async () => {
    vi.useFakeTimers()
    vi.spyOn(Math, 'random').mockReturnValue(0)
    const presence = vi.spyOn(collaborationClient, 'presence').mockResolvedValue({ people: [] })
    const mechanic: User = { ...user, role: 'mechanic', username: 'mech', tracker_login: null }
    const claimedByShiftmate = { ...issue, assignee: { display: 'Сменщик', login: 'other' } }

    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => claimedByShiftmate) }), currentUser: mechanic })
    await act(async () => { await vi.advanceTimersByTimeAsync(1) })
    expect(screen.getByRole('heading', { name: claimedByShiftmate.summary })).toBeVisible()
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })

    expect(presence).not.toHaveBeenCalled()
  })

  it('resolves a direct robot-check link for a shiftmates task to task without Emergency reads', async () => {
    const mechanic: User = { ...user, role: 'mechanic', username: 'mech', tracker_login: null }
    const claimedByShiftmate = { ...issue, assignee: { display: 'Сменщик', login: 'other' } }
    const emergencyResolve = vi.spyOn(api, 'emergencyResolve')
    renderWorkbench({
      client: apiClient({ trackerIssue: vi.fn(async () => claimedByShiftmate) }),
      currentState: { ...state, detailTab: 'check' },
      currentUser: mechanic,
    })

    expect(await screen.findByRole('heading', { name: claimedByShiftmate.summary })).toBeVisible()
    expect(screen.getByRole('tabpanel', { name: 'Задача' })).toBeVisible()
    expect(screen.queryByRole('tabpanel', { name: 'Проверка робота' })).not.toBeInTheDocument()
    expect(emergencyResolve).not.toHaveBeenCalled()
  })

  it('keeps the phone task summary, owner, latest comment and composer before secondary disclosures', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    const mechanic = { ...user, role: 'mechanic' as const }
    const currentIssue = { ...issue, assignee: { display: 'Operator', login: 'operator' } }
    const client = apiClient({
      trackerIssue: vi.fn(async () => currentIssue),
      trackerComments: vi.fn(async () => [
        { id: 'old', text: 'Старый комментарий', author: 'Сменщик', created_at: '2026-09-01T09:00:00Z' },
        { id: 'latest', text: 'Последняя важная деталь', author: 'Оператор', created_at: '2026-09-02T09:00:00Z' },
      ]),
    })
    vi.spyOn(collaborationClient, 'handoff').mockResolvedValue({
      revision: 0, done: '', remaining: '', obstacles: '', author: null, updated_at: null,
    })

    renderWorkbench({ client, currentUser: mechanic })

    expect(await screen.findByRole('heading', { name: currentIssue.summary })).toBeVisible()
    expect(screen.getAllByText(currentIssue.status).some(element => element.closest('.issue-detail'))).toBe(true)
    expect(screen.getByText('Operator')).toBeVisible()
    expect(screen.getByText('Последняя важная деталь')).toBeVisible()
    expect(screen.queryByText('Старый комментарий')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: ru.tracker.comments })).toBeVisible()
    for (const name of ['Использовать запчасть', 'Статус задачи', 'Исполнитель', ru.tracker.history]) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-expanded', 'false')
    }
    expect(screen.getByRole('button', { name: 'Передача смены' })).toHaveAttribute('aria-expanded', 'false')
    expect(document.querySelector('.issue-collaboration')).not.toBeInTheDocument()
    expect(screen.queryByText('Загружаем передачу смены…')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Передача смены' }))
    expect(document.querySelector('.issue-collaboration')).toBeInTheDocument()
  })

  it('offers a collapse control for the selected issue detail', async () => {
    renderWorkbench()

    expect(await screen.findByRole('button', { name: 'Свернуть: Задача ROBOPARK-42' })).toBeVisible()
  })

  it('never paints a released detail on a synchronous same-scope full remount', async () => {
    const client = apiClient()
    const first = renderWorkbench({ client })
    await screen.findByRole('heading', { name: issue.summary })
    vi.mocked(client.trackerIssue).mockImplementation(() => new Promise(() => undefined))
    first.unmount()
    renderWorkbench({ client })
    expect(screen.queryByRole('heading', { name: issue.summary })).not.toBeInTheDocument()
  })

  it.each(['pending', 'cached'] as const)('releases %s first-A detail across A unmount, B unmount, A', async (mode) => {
    const old = deferred<TrackerIssueDetail>()
    const fresh = deferred<TrackerIssueDetail>()
    const client = apiClient({ trackerIssue: vi.fn()
      .mockImplementationOnce(() => mode === 'pending' ? old.promise : Promise.resolve(issue))
      .mockResolvedValueOnce({ ...issue, summary: 'Область B' })
      .mockImplementationOnce(() => fresh.promise) })
    const first = renderWorkbench({ client })
    if (mode === 'cached') await screen.findByRole('heading', { name: issue.summary })
    first.unmount()
    const second = renderWorkbench({ client, selectedPark: { ...park, id: 8, tag: 'Beta' } })
    await screen.findByRole('heading', { name: 'Область B' })
    second.unmount()
    if (mode === 'pending') await act(async () => old.resolve(issue))
    renderWorkbench({ client })
    expect(screen.queryByRole('heading', { name: issue.summary })).not.toBeInTheDocument()
    await waitFor(() => expect(client.trackerIssue).toHaveBeenCalledTimes(3))
    await act(async () => fresh.resolve({ ...issue, summary: 'Свежая область A' }))
    expect(await screen.findByRole('heading', { name: 'Свежая область A' })).toBeInTheDocument()
  })

  it('keeps same-mounted-scope cached detail when selecting another issue and returning', async () => {
    const client = apiClient()
    const tree = (key: string) => <Harness><IssueWorkbench apiClient={client} user={user}
      selectedPark={park} state={state} issueKey={key} onCloseIssue={vi.fn()}
      onAuthorizationFailure={vi.fn(async () => undefined)} onOpenIssue={vi.fn()} onStateChange={vi.fn()} /></Harness>
    const view = render(tree(issue.key), { reactStrictMode: true })
    await screen.findByRole('heading', { name: issue.summary })
    vi.mocked(client.trackerIssue).mockImplementation(() => new Promise(() => undefined))
    view.rerender(tree('ROBOPARK-99'))
    view.rerender(tree(issue.key))
    expect(screen.getByRole('heading', { name: issue.summary })).toBeInTheDocument()
  })

  it('preserves the original blocker and active tab when resetting linked restrictions', async () => {
    const { onStateChange } = renderWorkbench({ currentState: { ...state, filters: { ...state.filters, status: 'queued', robot: '447' }, rootIssue: 'ROBOPARK-1', detailTab: 'open', checkTab: 'scheme' } })
    await screen.findByRole('heading', { name: 'Открытые задачи робота 447' })
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить ограничения' }))
    expect(onStateChange).toHaveBeenCalledWith({
      filters: { queue: 'ROBOPARK', status: 'queued' }, sort: 'oldest', page: 1,
      rootIssue: 'ROBOPARK-1', detailTab: 'open', checkTab: 'scheme',
    }, { replace: false })
  })

  it('keeps related repairs and robot diagnostics out of the main task until requested', async () => {
    const client = apiClient()
    renderWorkbench({ client })
    await screen.findByRole('heading', { name: issue.summary })
    expect(screen.getByRole('tab', { name: 'Задача' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Открытые задачи' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Открытые задачи робота 447' })).not.toBeInTheDocument()
    expect(client.trackerIssues).toHaveBeenCalledTimes(1)
    expect(document.querySelector('.robot-check')).toBeNull()
  })

  it('loads repairs without carrying the main blocker status, assignee or age restrictions', async () => {
    const client = apiClient()
    renderWorkbench({ client, currentState: {
      filters: { queue: 'ROBOPARK', status: 'closed', assignee: 'ivan', ageHours: 24 },
      sort: 'oldest', page: 3,
    } })
    fireEvent.click(await screen.findByRole('tab', { name: 'Открытые задачи' }))
    await waitFor(() => expect(client.trackerIssues).toHaveBeenLastCalledWith({
      queue: 'ROBOPARK', park: 'Alpha', robot_exact: '447', exclude_key: issue.key,
      related_repairs: true, open_only: true, sort: 'oldest', limit: 10, offset: 0,
    }))
  })

  it('preserves permitted untagged context for related repairs', async () => {
    const client = apiClient()
    renderWorkbench({ client, currentState: { ...state, detailTab: 'open', filters: { queue: 'ROBOPARK', untagged: true } } })
    await waitFor(() => expect(client.trackerIssues).toHaveBeenLastCalledWith(expect.objectContaining({
      robot_exact: '447', park: undefined, related_repairs: true,
    })))
  })

  it('does not invent robot identity from an issue summary', async () => {
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => ({
      ...issue, robot: null, summary: 'Проверить YASADR00000000447',
    })) }) })
    fireEvent.click(await screen.findByRole('tab', { name: 'Открытые задачи' }))
    expect(await screen.findByText('Робот в задаче не указан — связанные задачи недоступны.')).toBeVisible()
    expect(screen.queryByRole('link', { name: /Незавершённые задачи робота/ })).not.toBeInTheDocument()
  })

  it.each(['YASADR00000000447', 'yasadr00000000447', 'YASADR447', '00000000000447', '447', '0447', 'A447', 'a447', '[A447]', '[a447]', '[447]'])('loads normalized %s robot tasks only after the selected issue resolves, with open work before latest closed work', async robot => {
    const pendingDetail = deferred<TrackerIssueDetail>()
    const openTask: TrackerIssue = {
      ...issue,
      key: 'ROBOPARK-7',
      summary: 'Открытая задача робота',
      created_at: '2026-09-01T09:00:00Z',
    }
    const closedTask: TrackerIssue = {
      ...issue,
      key: 'ROBOPARK-8',
      summary: 'Последняя закрытая задача робота',
      status: 'Closed',
      created_at: '2026-09-03T09:00:00Z',
    }
    const client = apiClient({
      trackerIssue: vi.fn(() => pendingDetail.promise),
      trackerIssues: vi.fn()
        .mockResolvedValueOnce(page())
        .mockResolvedValueOnce(page([openTask]))
        .mockResolvedValueOnce(page([closedTask])),
    })

    renderWorkbench({ client })
    await waitFor(() => expect(client.trackerIssues).toHaveBeenCalledTimes(1))
    expect(screen.queryByText('Открытая задача робота')).not.toBeInTheDocument()

    await act(async () => {
      pendingDetail.resolve({ ...issue, robot })
    })

    expect(client.trackerIssues).toHaveBeenCalledTimes(1)
    fireEvent.click(await screen.findByRole('tab', { name: 'Открытые задачи' }))
    await screen.findByRole('heading', { name: 'Открытые задачи робота 447' })
    fireEvent.click(screen.getByRole('tab', { name: 'Закрытые задачи' }))
    await screen.findByRole('heading', { name: 'Закрытые задачи робота 447' })
    await waitFor(() => expect(client.trackerIssues).toHaveBeenCalledTimes(3))
    expect(client.trackerIssues).toHaveBeenNthCalledWith(2, expect.objectContaining({
      robot_exact: '447',
      exclude_key: issue.key,
      sort: 'oldest',
    }))
    expect(client.trackerIssues).toHaveBeenNthCalledWith(3, expect.objectContaining({
      robot_exact: '447',
      exclude_key: issue.key,
      sort: 'oldest',
      related_repairs: true,
      status: 'closed',
    }))
    expect(screen.queryByText(/Открытая задача робота/)).not.toBeInTheDocument()
    expect(screen.getByText(/Последняя закрытая задача робота/)).toBeVisible()
  })

  it.each(['other447', 'A447B', '[A447] extra', '447/448', '[A447', 'A447]'])('does not infer related robot tasks from ambiguous identifier %s', async robot => {
    const client = apiClient({ trackerIssue: vi.fn(async () => ({ ...issue, robot })) })
    renderWorkbench({ client, currentState: { ...state, detailTab: 'open' } })
    await screen.findByRole('heading', { name: `Задача ${issue.key}` })
    expect(screen.queryByRole('heading', { name: /Открытые задачи робота/ })).not.toBeInTheDocument()
    expect(client.trackerIssues).toHaveBeenCalledTimes(1)
  })

  it('keeps the selected issue and its capability-gated actions available when related robot work fails', async () => {
    const client = apiClient({
      trackerIssues: vi.fn()
        .mockResolvedValueOnce(page())
        .mockRejectedValueOnce(new ApiError(502, 'tracker_upstream_error'))
        .mockResolvedValueOnce(page()),
    })

    renderWorkbench({ client })

    expect(await screen.findByRole('heading', { name: issue.summary })).toBeVisible()
    expect(await screen.findByRole('button', { name: ru.tracker.actions.close })).toBeEnabled()
    fireEvent.click(screen.getByRole('tab', { name: 'Открытые задачи' }))
    expect(await screen.findByRole('heading', { name: 'Сервис временно недоступен' })).toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: 'Задача' }))
    expect(screen.getByRole('button', { name: ru.tracker.actions.close })).toBeEnabled()
  })

  it.each(['success', 'error'] as const)(
    'does not paint related %s state for robot A under pending robot B headings',
    async (mode) => {
      const pendingOpenB = deferred<Paged<TrackerIssue>>()
      const pendingClosedB = deferred<Paged<TrackerIssue>>()
      const aTask = { ...issue, key: 'ROBOPARK-A', summary: 'Данные робота A' }
      const client = apiClient({
        trackerIssues: vi.fn((query) => {
          if (query.robot_exact === '447' && !query.status) {
            return mode === 'success'
              ? Promise.resolve(page([aTask]))
              : Promise.reject(new ApiError(502, 'tracker_upstream_error'))
          }
          if (query.robot_exact === '447') return Promise.resolve(page())
          if (query.robot_exact === '448' && !query.status) return pendingOpenB.promise
          if (query.robot_exact === '448') return pendingClosedB.promise
          return Promise.resolve(page())
        }),
      })
      const commits: string[] = []
      render(<Harness><Profiler id="related" onRender={() => commits.push(document.body.textContent ?? '')}>
        <IssueWorkbench apiClient={client} issueKey={issue.key} onAuthorizationFailure={vi.fn(async () => undefined)}
          onCloseIssue={vi.fn()} onOpenIssue={vi.fn()} onStateChange={vi.fn()} selectedPark={park} state={{ ...state, detailTab: 'open' }} user={user} />
      </Profiler></Harness>)
      await waitFor(() => expect(client.trackerIssues).toHaveBeenCalledWith(expect.objectContaining({
        robot_exact: '447',
      })))
      if (mode === 'success') await screen.findByText(/Данные робота A/)
      else await screen.findByRole('heading', { name: 'Сервис временно недоступен' })

      const before = commits.length
      act(() => {
        resourceStore.set(`${accessPrefix()}issue:${issue.key}`, {
          ...issue,
          queue: 'ROBO-B',
          robot: '448',
        }, true)
      })

      await screen.findByRole('heading', { name: 'Открытые задачи робота 448' })
      await waitFor(() => expect(client.trackerIssues).toHaveBeenCalledWith(expect.objectContaining({
        robot_exact: '448',
        queue: 'ROBO-B',
      })))
      expect(commits.slice(before).every((text) => !text.includes('Данные робота A'))).toBe(true)
      expect(commits.slice(before).every((text) => !text.includes('Сервис временно недоступен'))).toBe(true)
    },
  )

  it.each(['permissions', 'read-revoked', 'tag', 'queue'] as const)(
    'isolates cached payload and actions after a same-ID %s change, including remount',
    async (change) => {
      const client = apiClient()
      const commits: string[] = []
      const renderTree = (currentUser: User, currentPark: Park) => (
        <Harness><Profiler id="access" onRender={() => commits.push(document.body.textContent ?? '')}>
          <IssueWorkbench apiClient={client} user={currentUser} selectedPark={currentPark}
            state={state} issueKey={issue.key} onAuthorizationFailure={vi.fn(async () => undefined)}
            onStateChange={vi.fn()} onOpenIssue={vi.fn()} onCloseIssue={vi.fn()} />
        </Profiler></Harness>
      )
      const view = render(renderTree(user, park))
      await screen.findByRole('heading', { name: issue.summary })
      const pendingDetail = deferred<TrackerIssueDetail>()
      const pendingList = deferred<Paged<TrackerIssue>>()
      vi.mocked(client.trackerIssue).mockImplementation(() => pendingDetail.promise)
      vi.mocked(client.trackerIssues).mockImplementation(() => pendingList.promise)
      const nextPark = { ...park, ...(change === 'tag' ? { tag: 'Beta' } : {}),
        ...(change === 'queue' ? { tracker_queue: 'NEWQUEUE' } : {}) }
      const nextUser = { ...user, parks: [nextPark], permissions: change === 'permissions'
        ? ['nav.tasks', 'tracker.read'] : change === 'read-revoked' ? ['nav.tasks'] : user.permissions }
      const before = commits.length
      view.rerender(renderTree(nextUser, nextPark))
      expect(commits.slice(before).every((text) => !text.includes(issue.summary))).toBe(true)
      expect(screen.queryByRole('button', { name: ru.tracker.actions.close })).not.toBeInTheDocument()
      view.unmount()
      render(renderTree(nextUser, nextPark))
      expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()
      await act(async () => {
        if (change === 'read-revoked') {
          pendingDetail.resolve(issue)
          pendingList.resolve(page())
        } else {
          pendingDetail.reject(new TypeError('offline'))
          pendingList.reject(new TypeError('offline'))
        }
      })
      expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()
      if (change === 'read-revoked') {
        expect(client.trackerIssue).toHaveBeenCalledTimes(1)
        expect(client.trackerIssues).toHaveBeenCalledTimes(1)
      }
    },
  )

  it.each(['success', '401'] as const)('does not reuse an old access in-flight %s for the new scope', async (result) => {
    const pending = deferred<TrackerIssueDetail>()
    const nextIssue = { ...issue, summary: 'Задача новой области', capabilities: { ...capabilities, close: false } }
    const client = apiClient({ trackerIssue: vi.fn().mockImplementationOnce(() => pending.promise).mockResolvedValue(nextIssue) })
    const onAuthorizationFailure = vi.fn(async () => undefined)
    const tree = (currentPark: Park) => <Harness><IssueWorkbench apiClient={client}
      user={{ ...user, parks: [currentPark] }} selectedPark={currentPark} state={state} issueKey={issue.key}
      onAuthorizationFailure={onAuthorizationFailure} onStateChange={vi.fn()} onOpenIssue={vi.fn()} onCloseIssue={vi.fn()} /></Harness>
    const view = render(tree(park), { reactStrictMode: true })
    const nextPark = { ...park, tag: 'Beta' }
    view.rerender(tree(nextPark))
    expect(await screen.findByRole('heading', { name: nextIssue.summary })).toBeInTheDocument()
    await act(async () => {
      if (result === 'success') pending.resolve(issue)
      else pending.reject(new ApiError(401))
    })
    expect(screen.getByRole('heading', { name: nextIssue.summary })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: ru.tracker.actions.close })).not.toBeInTheDocument()
    expect(onAuthorizationFailure).not.toHaveBeenCalled()
  })

  it.each([
    ['issue', 'success'], ['issue', '401'],
    ['park', 'success'], ['park', '401'],
    ['principal', 'success'], ['principal', '401'],
  ] as const)('ignores a pending close %s replacement followed by old %s', async (change, result) => {
    const pending = deferred<ReturnType<typeof actionResult>>()
    const nextIssue = { ...issue, key: change === 'issue' ? 'ROBOPARK-99' : issue.key, summary: 'Новый открытый экран' }
    const nextPark = change === 'park' ? { ...park, id: 8, tag: 'Beta' } : park
    const nextUser = change === 'principal' ? { ...user, id: 4, username: 'next-operator' } : user
    const client = apiClient({ trackerClose: vi.fn(() => pending.promise) })
    const onCloseIssue = vi.fn()
    const onAuthorizationFailure = vi.fn(async () => undefined)
    const tree = (replacement: boolean) => <Harness><IssueWorkbench apiClient={client}
      user={replacement ? nextUser : user} selectedPark={replacement ? nextPark : park}
      issueKey={replacement ? nextIssue.key : issue.key} state={state}
      onCloseIssue={onCloseIssue} onAuthorizationFailure={onAuthorizationFailure}
      onOpenIssue={vi.fn()} onStateChange={vi.fn()} /></Harness>
    const view = render(tree(false), { reactStrictMode: true })
    await screen.findByRole('heading', { name: issue.summary })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.actions.close }))
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить закрытие' }))
    await waitFor(() => expect(client.trackerClose).toHaveBeenCalledWith(issue.key, expect.objectContaining({ 'Idempotency-Key': expect.any(String), 'X-Tracker-State': expect.any(String) })))
    vi.mocked(client.trackerIssue).mockResolvedValue(nextIssue)
    view.rerender(tree(true))
    await screen.findByRole('heading', { name: nextIssue.summary })
    const invalidate = vi.spyOn(resourceStore, 'invalidate')
    const readCounts = [vi.mocked(client.trackerIssues).mock.calls.length, vi.mocked(client.trackerIssue).mock.calls.length]
    await act(async () => {
      if (result === 'success') pending.resolve(actionResult('close'))
      else pending.reject(new ApiError(401))
    })

    expect(onCloseIssue).not.toHaveBeenCalled()
    expect(onAuthorizationFailure).not.toHaveBeenCalled()
    expect(invalidate).not.toHaveBeenCalled()
    expect([vi.mocked(client.trackerIssues).mock.calls.length, vi.mocked(client.trackerIssue).mock.calls.length]).toEqual(readCounts)
    expect(screen.getByRole('heading', { name: nextIssue.summary })).toBeInTheDocument()
  })

  it('starts a fresh load when access returns before its obsolete first load finishes', async () => {
    const pending = deferred<TrackerIssueDetail>()
    const currentIssue = { ...issue, summary: 'Актуальная задача после возврата' }
    const client = apiClient({ trackerIssue: vi.fn()
      .mockImplementationOnce(() => pending.promise)
      .mockImplementationOnce(() => pending.promise)
      .mockResolvedValue(currentIssue) })
    const tree = (selectedPark: Park) => <Harness><IssueWorkbench apiClient={client} user={user}
      selectedPark={selectedPark} state={state} issueKey={issue.key} onCloseIssue={vi.fn()}
      onAuthorizationFailure={vi.fn(async () => undefined)} onOpenIssue={vi.fn()} onStateChange={vi.fn()} /></Harness>
    const view = render(tree(park), { reactStrictMode: true })
    view.rerender(tree({ ...park, tag: 'Beta' }))
    await screen.findByRole('heading', { name: currentIssue.summary })
    view.rerender(tree(park))
    // Initial setup + safe StrictMode replay, then B and returning A.
    await waitFor(() => expect(client.trackerIssue).toHaveBeenCalledTimes(4))
    await act(async () => { pending.resolve(issue) })
    expect(screen.getByRole('heading', { name: currentIssue.summary })).toBeInTheDocument()
  })

  it('still closes and refreshes the current owner after StrictMode re-setup', async () => {
    const pending = deferred<ReturnType<typeof actionResult>>()
    const client = apiClient({ trackerClose: vi.fn(() => pending.promise) })
    const { onCloseIssue } = renderWorkbench({ client, strictMode: true })
    await screen.findByRole('heading', { name: issue.summary })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.actions.close }))
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить закрытие' }))
    await waitFor(() => expect(client.trackerClose).toHaveBeenCalledOnce())
    await act(async () => { pending.resolve(actionResult('close')) })
    expect(onCloseIssue).toHaveBeenCalledOnce()
    expect(client.trackerIssue).toHaveBeenCalledTimes(3)
  })

  it('loads the URL-selected issue, exposes its robot and paginates through URL state', async () => {
    const client = apiClient()
    const { onStateChange } = renderWorkbench({ client })

    expect(
      await screen.findByRole('heading', { name: issue.summary }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Проверить робота 447' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Следующая страница' }))
    expect(onStateChange).toHaveBeenCalledWith(
      { filters: { queue: 'ROBOPARK' }, sort: 'oldest', page: 2 },
      { replace: false },
    )
    expect(client.trackerIssue).toHaveBeenCalledWith(issue.key)
  })

  it('uses one canonical search key to restore and save list scroll position', async () => {
    const canonicalSearch = buildWorkSearch(state, park.id)
    window.history.replaceState(
      {},
      '',
      `/work?sort=oldest&page=1&queue=ROBOPARK&park=${park.id}`,
    )
    saveWorkScroll(user.id, canonicalSearch, 384)

    const { container } = renderWorkbench({ selectedIssue: '' })
    const issueButton = await screen.findByRole('button', {
      name: `Открыть задачу ${issue.key}: ${issue.summary}`,
    })
    const scroller = container.querySelector<HTMLDivElement>('.rp-work-list-scroll')
    expect(scroller).not.toBeNull()
    expect(scroller?.scrollTop).toBe(384)

    if (scroller) scroller.scrollTop = 512
    fireEvent.click(issueButton)
    expect(readWorkScroll(user.id, canonicalSearch)).toBe(512)
  })

  it('restores and saves document scroll below 900px without detail cleanup erasing it', async () => {
    vi.stubGlobal('innerWidth', 390)
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    vi.stubGlobal('scrollY', 540)
    const canonicalSearch = buildWorkSearch(state, park.id)
    saveWorkScroll(user.id, canonicalSearch, 384)
    seedCurrentWork()
    const list = renderWorkbench({ selectedIssue: '', strictMode: true })
    const row = await screen.findByRole('button', { name: `Открыть задачу ${issue.key}: ${issue.summary}` })
    await waitFor(() => expect(scrollTo).toHaveBeenCalledWith({ top: 384, behavior: 'instant' }))
    fireEvent.click(row)
    expect(readWorkScroll(user.id, canonicalSearch)).toBe(540)
    list.unmount()
    vi.stubGlobal('scrollY', 0)
    scrollTo.mockClear()
    const detail = renderWorkbench()
    await screen.findByRole('heading', { name: issue.summary })
    detail.unmount()
    expect(scrollTo).not.toHaveBeenCalled()
    expect(readWorkScroll(user.id, canonicalSearch)).toBe(540)
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

  it('invalidates the owned list and selected issue resources after mutation', async () => {
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
    expect(invalidate).toHaveBeenCalledTimes(6)
    expect(invalidate).toHaveBeenCalledWith(`${accessPrefix()}list:${park.id}:`, {
      prefix: true,
    })
    expect(invalidate).toHaveBeenCalledWith(`${accessPrefix()}owned:${user.username}`)
    expect(invalidate).toHaveBeenCalledWith(`${accessPrefix()}issue:${issue.key}`)
    expect(invalidate).toHaveBeenCalledWith(`${accessPrefix()}comments:${issue.key}`)
    expect(invalidate).toHaveBeenCalledWith(`${accessPrefix()}transitions:${issue.key}`)
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
      expect(resourceStore.get(`${accessPrefix()}issue:${issue.key}`)).toBeUndefined()
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
    const client = apiClient()
    renderWorkbench({
      client,
      currentState: {
        filters: { queue: 'ROBOPARK', untagged: true },
        sort: 'oldest',
        page: 1,
      },
      currentUser: customUser,
      selectedIssue: '',
    })

    await waitFor(() => expect(screen.queryByText(ru.loading)).not.toBeInTheDocument())
    expect(screen.queryByRole('checkbox', { name: 'Без тега парка' })).not.toBeInTheDocument()
    expect(client.trackerIssues).toHaveBeenCalledWith(expect.objectContaining({
      park: park.tag,
      untagged: undefined,
    }))
    expect(resourceStore.get(listKey(customUser))).toEqual(page())
    expect(resourceStore.get(listKey(customUser, park, {
      filters: { queue: 'ROBOPARK', untagged: true },
      sort: 'oldest',
      page: 1,
    }))).toBeUndefined()
  })

  it.each(['operator', 'admin', 'royal'] as const)(
    'keeps the untagged request scope for the %s system role',
    async (role) => {
      const client = apiClient()
      renderWorkbench({
        client,
        currentState: {
          filters: { queue: 'ROBOPARK', untagged: true },
          sort: 'oldest',
          page: 1,
        },
        currentUser: { ...user, role },
        selectedIssue: '',
      })

      await waitFor(() => expect(screen.queryByText(ru.loading)).not.toBeInTheDocument())
      expect(client.trackerIssues).toHaveBeenCalledWith(expect.objectContaining({
        park: undefined,
        untagged: true,
      }))
    },
  )
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
    client?: WorkPageApiClient
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

describe('WorkPage authorization lifetime', () => {
  function renderRefreshingPage(client: WorkPageApiClient) {
    let replaceUser: (next: User) => void = () => undefined
    const refreshUser = vi.fn(async () => {
      const refreshed = { ...user, parks: [...user.parks] }
      // A second refresh does not publish again, so a regression cannot loop forever.
      if (refreshUser.mock.calls.length === 1) replaceUser(refreshed)
      return refreshed
    })

    function LiveAuth() {
      const [currentUser, setCurrentUser] = useState(user)
      useLayoutEffect(() => {
        replaceUser = setCurrentUser
      }, [])
      return (
        <AuthContext.Provider value={{
          user: currentUser,
          loading: false,
          login: vi.fn(async () => currentUser),
          logout: vi.fn(async () => undefined),
          refreshUser,
        }}>
          <ParkScopeProvider>
            <Routes>
              <Route element={<WorkPage apiClient={client} />} path="/work" />
            </Routes>
          </ParkScopeProvider>
        </AuthContext.Provider>
      )
    }

    render(<MemoryRouter initialEntries={['/work?park=7']}><LiveAuth /></MemoryRouter>)
    return { refreshUser, replaceUser: (next: User) => replaceUser(next) }
  }

  it('keeps one exact denial across a same-principal auth refresh and park reselection', async () => {
    seedCurrentWork()
    resourceStore.set('work:30:list:7:other', { secret: 'other-user' }, true)
    resourceStore.set('overview:3', { secret: 'other-domain' }, true)
    const client = apiClient({
      trackerIssues: vi.fn(async () => {
        throw new ApiError(403, 'tracker_park_forbidden', 'req-work-denied')
      }),
    })
    const { refreshUser } = renderRefreshingPage(client)

    await screen.findByRole('heading', { name: 'Нет доступа' })
    await act(async () => { await Promise.resolve() })

    expect(client.trackerIssues).toHaveBeenCalledTimes(1)
    expect(refreshUser).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('alert')).toHaveTextContent('Нет доступа к этому парку.')
    expect(screen.getByRole('alert')).toHaveTextContent('Код запроса: req-work-denied')
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
    expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()
    expect(resourceStore.get(listKey())).toBeUndefined()
    expect(resourceStore.get('work:30:list:7:other')).toEqual({ secret: 'other-user' })
    expect(resourceStore.get('overview:3')).toEqual({ secret: 'other-domain' })
  })

  it('resets the denial only when the actual principal changes', async () => {
    const client = apiClient({
      trackerIssues: vi.fn(async () => {
        throw new ApiError(403, 'tracker_park_forbidden', 'req-old-principal')
      }),
    })
    const { refreshUser, replaceUser } = renderRefreshingPage(client)
    await screen.findByRole('heading', { name: 'Нет доступа' })
    await act(async () => { await Promise.resolve() })

    vi.mocked(client.trackerIssues).mockResolvedValue(page())
    act(() => replaceUser({ ...user, id: 4, username: 'operator-2' }))

    expect(await screen.findByRole('button', {
      name: `Открыть задачу ${issue.key}: ${issue.summary}`,
    })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Нет доступа' })).not.toBeInTheDocument()
    expect(screen.queryByText('Код запроса: req-old-principal')).not.toBeInTheDocument()
    expect(refreshUser).toHaveBeenCalledTimes(1)
  })
})

beforeEach(() => { vi.stubGlobal('crypto', webcrypto) })

it('does not send a mutation if the principal changes while its payload is being hashed', async () => {
  const hash = deferred<ArrayBuffer>()
  vi.spyOn(crypto.subtle, 'digest').mockReturnValueOnce(hash.promise)
  const client = apiClient()
  const tree = (nextUser = user) => <Harness><IssueWorkbench apiClient={client}
    user={nextUser} selectedPark={park} issueKey={issue.key} state={state}
    onCloseIssue={vi.fn()} onAuthorizationFailure={vi.fn(async () => undefined)}
    onOpenIssue={vi.fn()} onStateChange={vi.fn()} /></Harness>
  const view = render(tree())
  await screen.findByRole('heading', { name: issue.summary })
  fireEvent.click(screen.getByRole('button', { name: ru.tracker.actions.close }))
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить закрытие' }))
  view.rerender(tree({ ...user, id: 99, username: 'different-principal' }))
  await act(async () => { hash.resolve(new ArrayBuffer(32)) })
  expect(client.trackerClose).not.toHaveBeenCalled()
})

it('refreshes a conflicting status immediately and preserves the comment draft for review', async () => {
  const client = apiClient({ trackerComment: vi.fn(async () => { throw new ApiError(409, 'tracker_state_conflict') }) })
  renderWorkbench({ client })
  await screen.findByRole('heading', { name: issue.summary })
  vi.mocked(client.trackerIssue).mockResolvedValue({ ...issue, status: 'На проверке', status_key: 'review' })
  fireEvent.change(screen.getByRole('textbox', { name: ru.tracker.comments }), { target: { value: 'Не потерять этот черновик' } })
  fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
  await screen.findByText(/Статус или исполнитель изменились/)
  await screen.findByText('На проверке')
  expect(screen.getByRole('textbox', { name: ru.tracker.comments })).toHaveValue('Не потерять этот черновик')
})
