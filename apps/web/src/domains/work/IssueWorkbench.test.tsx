import { readFileSync } from 'node:fs'
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
  type TaskActionResult,
  type TaskTimelineItem,
  type User,
} from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { ParkScopeProvider } from '../../app/park/ParkScopeProvider'
import { InterfaceModeProvider } from '../../app/interface/InterfaceModeProvider'
import { createInterfaceModeStore } from '../../app/interface/interfaceModeStore'
import { ru } from '../../i18n/ru'
import { collaborationClient } from '../../components/tracker/collaborationClient'
import { resetCoalescingForTests, resourceStore } from '../../lib/resource'
import { IssueWorkbench, type IssueWorkbenchApiClient } from './IssueWorkbench'
import { WorkPage } from './WorkPage'
import { SyncContextProvider, SyncProvider, type SyncContextValue, type SyncEngineLike } from '../../pwa/SyncProvider'
import type { OfflineAction } from '../../pwa/offlineTypes'
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
  timezone: 'Europe/Moscow',
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

const queuedWorkflowIssue: TrackerIssueDetail = {
  ...issue,
  workflow: { owner: null, review_state: null, display_status: 'queued', sync_state: 'saved', has_current_cycle_comment: false },
}

const reviewWorkflowIssue: TrackerIssueDetail = {
  ...queuedWorkflowIssue,
  workflow: { ...queuedWorkflowIssue.workflow!, review_state: 'pending', display_status: 'review' },
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

function taskActionResult(action: string): TaskActionResult {
  return { ...actionResult(action), sync_state: 'saved', workflow: reviewWorkflowIssue.workflow! }
}

function taskMessageResult(text: string): TaskTimelineItem {
  return { id: 'message-1', kind: 'user', author: user.username, text, created_at: '2026-09-02T09:00:00Z', sync_state: 'saved', attachments: [] }
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
    taskRetryNow: vi.fn(async () => ({ ...actionResult('retry_now'), sync_state: 'pending' as const, workflow: null })),
    taskHide: vi.fn(async () => ({ ...actionResult('hide'), sync_state: 'saved' as const, workflow: null })),
    taskRestore: vi.fn(async () => ({ ...actionResult('restore'), sync_state: 'saved' as const, workflow: null })),
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

it('keeps transfer and review locked until Tracker confirms the mechanic claim', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic' }
  const pending: TrackerIssueDetail = {
    ...issue,
    assignee: { display: 'mech', login: 'mech' },
    claim: { park_id: park.id, state: 'pending' },
    workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'pending', has_current_cycle_comment: true },
  }
  const taskRepairOptions = vi.fn(async () => { throw new ApiError(502, 'tracker_upstream_error') })
  renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => pending), taskRepairOptions }) })

  expect(await screen.findByText(/Tracker ещё подтверждает взятие задачи/)).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Передать на проверку' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Передать смену' })).not.toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Проверка' })).toBeVisible()
  // A pre-transition snapshot would be stale as soon as UNSORTED is written.
  expect(taskRepairOptions).not.toHaveBeenCalled()
})

it('lets an admin retry a task needing attention once and announces recovery', async () => {
  const admin = { ...user, role: 'admin' as const }
  const attention = {
    ...issue,
    workflow: { owner: null, review_state: null, display_status: 'queued' as const, sync_state: 'needs_attention' as const, has_current_cycle_comment: false },
  }
  const taskRetryNow = vi.fn(async () => ({ ...actionResult('retry_now'), sync_state: 'pending' as const, workflow: attention.workflow }))
  const client = apiClient({ trackerIssue: vi.fn(async () => attention), taskRetryNow })

  renderWorkbench({ client, currentUser: admin })
  fireEvent.click(await screen.findByRole('button', { name: 'Повторить сейчас' }))

  await screen.findByText('Повторная отправка запущена')
  expect(taskRetryNow).toHaveBeenCalledOnce()
})

it('requires a reason to hide and restores a hidden task without loading its timeline', async () => {
  const admin = { ...user, role: 'admin' as const }
  const taskHide = vi.fn(async () => ({ ...actionResult('hide'), sync_state: 'saved' as const, workflow: null }))
  const visibleClient = apiClient({
    trackerIssue: vi.fn(async () => ({ ...issue, workflow: { owner: null, review_state: null, display_status: 'queued' as const, sync_state: 'saved' as const, has_current_cycle_comment: false } })),
    taskHide,
  })
  const visible = renderWorkbench({ client: visibleClient, currentUser: admin })

  fireEvent.click(await screen.findByRole('button', { name: 'Скрыть задачу' }))
  expect(screen.getByRole('button', { name: 'Подтвердить скрытие' })).toBeDisabled()
  fireEvent.change(screen.getByRole('textbox', { name: 'Причина скрытия' }), { target: { value: 'Дубль' } })
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить скрытие' }))
  await waitFor(() => expect(taskHide).toHaveBeenCalledOnce())
  visible.unmount()

  const hidden = {
    ...issue,
    workflow: { owner: null, review_state: null, display_status: 'hidden' as const, sync_state: 'saved' as const, has_current_cycle_comment: false,
      hidden: { reason: 'Дубль', actor: 'admin', created_at: '2026-09-15T09:00:00Z' } },
  }
  const taskTimeline = vi.fn(async () => [])
  const taskRestore = vi.fn(async () => ({ ...actionResult('restore'), sync_state: 'saved' as const, workflow: null }))
  const hiddenClient = apiClient({ trackerIssue: vi.fn(async () => hidden), taskTimeline, taskRestore })
  renderWorkbench({ client: hiddenClient, currentUser: admin, currentState: { ...state, filters: { ...state.filters, includeHidden: true } } })

  expect(await screen.findByText('Причина скрытия: Дубль')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: 'Восстановить задачу' }))
  await waitFor(() => expect(taskRestore).toHaveBeenCalledOnce())
  expect(taskTimeline).not.toHaveBeenCalled()
})

it('keeps hidden detail and restore controls on the same phone mount after hiding', async () => {
  vi.stubGlobal('innerWidth', 390)
  const admin = { ...user, role: 'admin' as const }
  const visible = {
    ...issue,
    workflow: { owner: null, review_state: null, display_status: 'queued' as const, sync_state: 'saved' as const, has_current_cycle_comment: false },
  }
  const hidden = {
    ...issue,
    workflow: { ...visible.workflow, display_status: 'hidden' as const,
      hidden: { reason: 'Дубль с телефона', actor: 'admin', created_at: '2026-09-15T09:00:00Z' } },
  }
  const trackerIssue = vi.fn().mockResolvedValueOnce(visible).mockResolvedValue(hidden)
  const taskTimeline = vi.fn()
    .mockResolvedValueOnce([])
    .mockRejectedValue(new ApiError(404, 'task_not_found', 'hidden-comments'))
  const taskHide = vi.fn(async () => ({ ...actionResult('hide'), sync_state: 'saved' as const, workflow: hidden.workflow }))
  const client = apiClient({ trackerIssue, taskTimeline, taskHide })

  renderWorkbench({
    client,
    currentUser: admin,
    currentState: { ...state, filters: { ...state.filters, includeHidden: true } },
  })
  fireEvent.click(await screen.findByRole('button', { name: 'Скрыть задачу' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Причина скрытия' }), {
    target: { value: 'Дубль с телефона' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Подтвердить скрытие' }))

  expect(await screen.findByText('Причина скрытия: Дубль с телефона')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Восстановить задачу' })).toBeVisible()
  expect(taskTimeline).toHaveBeenCalledOnce()
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

it('allows a mechanic to inspect a task before claiming it for changes', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: 'mech.login' }
  const unassigned = { ...issue, assignee: null }
  const client = apiClient({ trackerIssues: vi.fn(async () => page([unassigned])) })
  renderWorkbench({ client, selectedIssue: '', currentUser: mechanic })

  const take = await screen.findByRole('button', { name: 'Взять в работу' })
  expect(screen.getByRole('button', { name: /Открыть задачу/ })).toBeVisible()
  fireEvent.click(take)
  await waitFor(() => expect(client.trackerAssign).toHaveBeenCalledWith(issue.key, 'mech1'))
})

it('lets a mechanic claim an unassigned open task in another status through the reliable action', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: 'mech.login' }
  const diagnostic = { ...issue, status: 'Диагностика', status_key: 'diagnostics', assignee: null }
  const taskClaim = vi.fn(async () => taskActionResult('claim'))
  const client = apiClient({ trackerIssues: vi.fn(async () => page([diagnostic])), taskClaim })
  renderWorkbench({ client, selectedIssue: '', currentUser: mechanic, currentState: { filters: { queue: 'ROBOPARK', status: 'diagnostics' }, sort: 'oldest', page: 1 } })

  fireEvent.click(await screen.findByRole('button', { name: 'Взять в работу' }))
  await waitFor(() => expect(taskClaim).toHaveBeenCalledWith(issue.key, expect.any(String)))
})

it('queues a mechanic claim locally and does not treat it as owned before confirmation', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const taskClaim = vi.fn(async () => taskActionResult('claim'))
  let queued: OfflineAction | undefined
  const enqueueAction = vi.fn(async (input: Parameters<SyncContextValue['enqueueAction']>[0]) => {
    queued = { ...input, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 }
    return queued
  })
  const sync = {
    state: { status: 'offline' as const, pending: 0, conflicts: 0 },
    actionTrackingReady: true,
    enqueueAction,
    enqueueMedia: vi.fn(),
    syncNow: vi.fn(async () => false),
    cancelAction: vi.fn(async () => undefined),
    resolveConflict: vi.fn(async () => undefined),
    findAction: vi.fn(async () => queued),
    subscribeAction: vi.fn(() => () => undefined),
  } satisfies SyncContextValue
  const view = renderWorkbench({
    client: apiClient({ trackerIssues: vi.fn(async () => page([{ ...issue, assignee: null }])), taskClaim }),
    currentUser: mechanic,
    selectedIssue: '',
    sync,
  })

  fireEvent.click(await screen.findByRole('button', { name: 'Взять в работу' }))

  await waitFor(() => expect(enqueueAction).toHaveBeenCalledWith(expect.objectContaining({
    action: 'claim', resourceType: 'tracker_issue', resourceId: issue.key,
    payload: { park_id: park.id },
  })))
  expect(taskClaim).not.toHaveBeenCalled()
  expect(view.onOpenIssue).not.toHaveBeenCalled()
  expect(await screen.findByText('Взятие ожидает подтверждения')).toBeVisible()
})

it('does not bypass a still-initializing offline queue with a direct claim request', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const taskClaim = vi.fn(async () => taskActionResult('claim'))
  const sync = {
    state: { status: 'idle' as const, pending: 0, conflicts: 0 },
    actionTrackingReady: false,
    enqueueAction: vi.fn(), enqueueMedia: vi.fn(), syncNow: vi.fn(async () => false),
    cancelAction: vi.fn(async () => undefined), resolveConflict: vi.fn(async () => undefined),
    findAction: vi.fn(async () => undefined), subscribeAction: vi.fn(() => () => undefined),
  } satisfies SyncContextValue
  renderWorkbench({
    client: apiClient({ trackerIssues: vi.fn(async () => page([{ ...issue, assignee: null }])), taskClaim }),
    currentUser: mechanic, selectedIssue: '', sync,
  })

  expect(await screen.findByRole('button', { name: 'Взять в работу' })).toBeDisabled()
  expect(taskClaim).not.toHaveBeenCalled()
  expect(sync.enqueueAction).not.toHaveBeenCalled()
})

it('keeps claim controls closed when saved claim hydration fails', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const sync = {
    state: { status: 'offline' as const, pending: 0, conflicts: 0 },
    actionTrackingReady: true,
    enqueueAction: vi.fn(), enqueueMedia: vi.fn(), syncNow: vi.fn(async () => false),
    cancelAction: vi.fn(async () => undefined), resolveConflict: vi.fn(async () => undefined),
    findAction: vi.fn(async () => { throw new Error('indexeddb_unavailable') }),
    subscribeAction: vi.fn(() => () => undefined),
  } satisfies SyncContextValue
  renderWorkbench({
    client: apiClient({ trackerIssues: vi.fn(async () => page([{ ...issue, assignee: null }])) }),
    currentUser: mechanic, selectedIssue: '', sync,
  })

  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось проверить сохранённое назначение')
  expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument()
  expect(sync.enqueueAction).not.toHaveBeenCalled()
})

it('restores a queued claim after reopening work and refreshes only after confirmation', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const pending: OfflineAction = {
    id: 'claim-persisted-42', deviceId: 'local', resourceType: 'tracker_issue',
    resourceId: issue.key, action: 'claim', idempotencyKey: 'claim-persisted-42',
    baseRevision: null, dependencies: [], payload: { park_id: park.id },
    state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1,
  }
  let stored: OfflineAction | undefined = pending
  let notify: ((action: OfflineAction | undefined) => void) | undefined
  const trackerIssues = vi.fn(async () => page([{ ...issue, assignee: null }]))
  const sync = {
    state: { status: 'offline' as const, pending: 1, conflicts: 0 },
    actionTrackingReady: true,
    enqueueAction: vi.fn(), enqueueMedia: vi.fn(),
    syncNow: vi.fn(async () => false), cancelAction: vi.fn(async () => undefined),
    resolveConflict: vi.fn(async () => undefined),
    findAction: vi.fn(async () => stored),
    subscribeAction: vi.fn((_id: string, callback: (action: OfflineAction | undefined) => void) => {
      notify = callback
      return () => undefined
    }),
  } satisfies SyncContextValue
  renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '', sync,
  })

  expect(await screen.findByText('Взятие ожидает подтверждения')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument()
  expect(sync.enqueueAction).not.toHaveBeenCalled()
  // The visible pending state can precede the subscription effect. Send the
  // mocked confirmation only after there is a subscriber to receive it.
  await waitFor(() => expect(notify).toBeTypeOf('function'))
  const readsBeforeConfirmation = trackerIssues.mock.calls.length
  await act(async () => {
    stored = undefined
    notify?.({ ...pending, state: 'confirmed' })
  })
  await waitFor(() => expect(trackerIssues.mock.calls.length).toBeGreaterThan(readsBeforeConfirmation))
  expect(screen.queryByText('Взятие ожидает подтверждения')).not.toBeInTheDocument()
})

it('shows a persisted claim conflict without offering a duplicate claim', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const conflicted: OfflineAction = {
    id: 'claim-conflict-42', deviceId: 'local', resourceType: 'tracker_issue',
    resourceId: issue.key, action: 'claim', idempotencyKey: 'claim-conflict-42',
    baseRevision: null, dependencies: [], payload: { park_id: park.id },
    state: 'conflict', attempts: 1, createdAt: 1, updatedAt: 2,
  }
  const enqueueAction = vi.fn()
  const sync = {
    state: { status: 'attention' as const, pending: 0, conflicts: 1 },
    actionTrackingReady: true,
    enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(async () => false),
    cancelAction: vi.fn(async () => undefined), resolveConflict: vi.fn(async () => undefined),
    findAction: vi.fn(async () => conflicted), subscribeAction: vi.fn(() => () => undefined),
  } satisfies SyncContextValue
  renderWorkbench({
    client: apiClient({ trackerIssues: vi.fn(async () => page([{ ...issue, assignee: null }])) }),
    currentUser: mechanic, selectedIssue: '', sync,
  })

  expect(await screen.findByRole('alert', { name: 'Взятие задачи требует внимания' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Взять в работу' })).not.toBeInTheDocument()
  expect(enqueueAction).not.toHaveBeenCalled()
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
      ? { ...page([owned]), total: 1, has_more: false }
      : { ...page([queued, owned]), total: 2, has_more: false }
  ))
  renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '',
    currentState: { filters: { queue: 'ROBOPARK', status: 'queued' }, sort: 'oldest', page: 1 },
  })

  expect(await screen.findByRole('heading', { name: 'Очередь парка' })).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: /Мои задачи \(1\)/ }))
  expect(screen.getAllByRole('article')).toHaveLength(1)
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-OWNED/ })).toBeVisible()
  expect(screen.queryByRole('button', { name: /Открыть задачу ROBOPARK-QUEUE/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Очередь' }))
  const rows = screen.getAllByRole('article')
  expect(rows.map(row => row.textContent)).toEqual([
    expect.stringContaining('ROBOPARK-QUEUE'),
    expect.stringContaining('ROBOPARK-OWNED'),
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

it('does not show a false zero for owned tasks while their request is pending', async () => {
  const mechanic: User = { ...user, username: 'mech-loading', role: 'mechanic', tracker_login: null }
  let completeOwned!: (value: Paged<TrackerIssue>) => void
  const ownedPending = new Promise<Paged<TrackerIssue>>(resolve => { completeOwned = resolve })
  const trackerIssues = vi.fn((query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) =>
    query.owned_by_me ? ownedPending : Promise.resolve(page([])))
  renderWorkbench({ client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '' })

  expect(await screen.findByRole('button', { name: 'Мои задачи' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Мои задачи (0)' })).not.toBeInTheDocument()
  await act(async () => { completeOwned(page([])) })
  expect(await screen.findByRole('button', { name: 'Мои задачи (0)' })).toBeVisible()
})

it('offers all open park tasks when the mechanic priority queue is empty', async () => {
  const mechanic: User = { ...user, username: 'mech-empty-queue', role: 'mechanic', tracker_login: null }
  const trackerIssues = vi.fn(async () => page([]))
  const view = renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '',
    currentState: { filters: { queue: 'ROBOPARK', status: 'queued' }, sort: 'oldest', page: 1 },
  })

  const showAll = await screen.findByRole('button', { name: 'Показать все открытые задачи' })
  fireEvent.click(showAll)

  expect(view.onStateChange).toHaveBeenCalledWith({
    filters: { queue: 'ROBOPARK', status: undefined }, sort: 'oldest', page: 1,
  }, { replace: false })
  await waitFor(() => expect(trackerIssues).toHaveBeenCalledWith(expect.objectContaining({
    status: undefined, open_only: true, sort: 'queue_first',
  })))
})

it('puts the oldest queued park work before a mechanic’s started repairs', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owner = { display: 'mech1', login: 'mech1' }
  const owned = { ...issue, key: 'ROBOPARK-OWNED', assignee: owner, status_key: 'in_progress' }
  const newer = { ...issue, key: 'ROBOPARK-NEWER', queued_at: '2026-09-02T09:00:00Z', sla_source: 'status_history' as const }
  const older = { ...issue, key: 'ROBOPARK-OLDER', queued_at: '2026-09-01T09:00:00Z', sla_source: 'status_history' as const }
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) =>
    query.owned_by_me ? page([owned]) : page([newer, older]))
  renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '',
    currentState: { filters: { queue: 'ROBOPARK', status: 'queued' }, sort: 'oldest', page: 1 },
  })

  expect(await screen.findByText('ROBOPARK-OLDER')).toBeVisible()
  expect(screen.getAllByRole('article').map(row => row.textContent)).toEqual([
    expect.stringContaining('ROBOPARK-OLDER'),
    expect.stringContaining('ROBOPARK-NEWER'),
    expect.stringContaining('ROBOPARK-OWNED'),
  ])
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-OLDER:/ })).toBeVisible()
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-NEWER:/ })).toBeVisible()
  const queuePages = screen.getByRole('navigation', { name: 'Страницы задач' })
  const ownedRow = screen.getAllByRole('article')[2]
  expect(queuePages.compareDocumentPosition(ownedRow) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it('explains an empty park queue while keeping several owned open tasks visible', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owner = { display: 'mech1', login: 'mech1' }
  const owned = [
    { ...issue, key: 'ROBOPARK-OWNED-1', assignee: owner },
    { ...issue, key: 'ROBOPARK-OWNED-2', assignee: owner, status_key: 'review' },
  ]
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) =>
    query.owned_by_me ? page(owned) : page([]))
  renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '',
    currentState: { filters: { queue: 'ROBOPARK', status: 'queued' }, sort: 'oldest', page: 1 },
  })

  expect(await screen.findByRole('heading', { name: 'Мои открытые задачи' })).toBeVisible()
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-OWNED-1/ })).toBeVisible()
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-OWNED-2/ })).toBeVisible()
  expect(screen.getByText('В очереди парка сейчас нет задач.')).toBeVisible()
  expect(screen.getByText('Задач по фильтру: 0')).toBeVisible()
})

it('keeps concurrent tasks on different robots visible in My Tasks', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owner = { display: 'mech1', login: 'mech1' }
  const first = { ...issue, key: 'ROBOPARK-ROBOT-447', robot: '447', assignee: owner }
  const second = { ...issue, key: 'ROBOPARK-ROBOT-448', robot: '448', assignee: owner }
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) =>
    query.owned_by_me ? { ...page([first, second]), total: 2, has_more: false } : page([]))
  renderWorkbench({ client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '' })

  fireEvent.click(await screen.findByRole('button', { name: /Мои задачи \(2\)/ }))
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-ROBOT-447/ })).toBeVisible()
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-ROBOT-448/ })).toBeVisible()
})

it('loads additional owned tasks only when their next page is requested', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owner = { display: 'mech1', login: 'mech1' }
  const firstPage = Array.from({ length: 50 }, (_, index) => ({
    ...issue, key: `ROBOPARK-OWNED-${index + 1}`, assignee: owner,
  }))
  const last = { ...issue, key: 'ROBOPARK-OWNED-51', assignee: owner }
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) => {
    if (!query.owned_by_me) return page([])
    return {
      items: query.offset === 50 ? [last] : firstPage,
      total: 51, limit: 50, offset: query.offset ?? 0, has_more: query.offset !== 50,
    }
  })
  renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '',
    currentState: { filters: { queue: 'ROBOPARK', status: 'queued' }, sort: 'oldest', page: 1 },
  })

  const mine = await screen.findByRole('button', { name: 'Мои задачи (51)' })
  expect(trackerIssues.mock.calls.filter(([query]) => query.owned_by_me && query.offset === 50)).toHaveLength(0)
  fireEvent.click(mine)
  fireEvent.click(screen.getByRole('button', { name: 'Следующая страница моих задач' }))
  expect(await screen.findByRole('button', { name: /Открыть задачу ROBOPARK-OWNED-51/ })).toBeVisible()
  expect(screen.queryByRole('button', { name: /Открыть задачу ROBOPARK-OWNED-1:/ })).not.toBeInTheDocument()
  expect(trackerIssues).toHaveBeenCalledWith(expect.objectContaining({ owned_by_me: true, limit: 50, offset: 50 }))
  fireEvent.click(screen.getByRole('button', { name: 'Предыдущая страница моих задач' }))
  expect(await screen.findByRole('button', { name: /Открыть задачу ROBOPARK-OWNED-1:/ })).toBeVisible()
})

it('shows a retryable error for an owned task page without replacing it with an empty state', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owner = { display: 'mech1', login: 'mech1' }
  const owned = { ...issue, key: 'ROBOPARK-OWNED', assignee: owner }
  let secondPageAttempts = 0
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) => {
    if (!query.owned_by_me) return page([])
    if (query.offset === 50) {
      secondPageAttempts += 1
      if (secondPageAttempts === 1) throw new ApiError(502, 'tracker_upstream_error', 'owned-page-2')
      return { items: [owned], total: 51, limit: 50, offset: 50, has_more: false }
    }
    return { items: [owned], total: 51, limit: 50, offset: 0, has_more: true }
  })
  renderWorkbench({ client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '' })

  fireEvent.click(await screen.findByRole('button', { name: 'Мои задачи (51)' }))
  fireEvent.click(screen.getByRole('button', { name: 'Следующая страница моих задач' }))
  expect(await screen.findByText('Ошибка интеграции со Startrek.')).toBeVisible()
  expect(screen.queryByRole('heading', { name: 'Моих задач пока нет' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  expect(await screen.findByRole('button', { name: /Открыть задачу ROBOPARK-OWNED/ })).toBeVisible()
  expect(secondPageAttempts).toBe(2)
})

it('calls the all-status park list open tasks rather than only queue', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owned = { ...issue, key: 'ROBOPARK-OWNED', assignee: { display: 'mech1', login: 'mech1' } }
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) =>
    query.owned_by_me ? page([owned]) : page([issue]))
  renderWorkbench({ client: apiClient({ trackerIssues }), currentUser: mechanic, selectedIssue: '', currentState: state })

  expect(await screen.findByRole('heading', { name: /^Открытые задачи$/ })).toBeVisible()
  expect(screen.getByRole('button', { name: /^Задачи парка$/ })).toBeVisible()
  expect(await screen.findByRole('heading', { name: 'Открытые задачи парка' })).toBeVisible()
})

it('opens My Tasks directly from the mechanic navigation URL', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic', tracker_login: null }
  const owned = { ...issue, key: 'ROBOPARK-OWNED', assignee: { display: 'mech1', login: 'mech1' } }
  const queued = { ...issue, key: 'ROBOPARK-QUEUE' }
  const trackerIssues = vi.fn(async (query: Parameters<IssueWorkbenchApiClient['trackerIssues']>[0]) => page(query.owned_by_me ? [owned] : [queued]))
  const client = apiClient({ trackerIssues })
  renderWorkbench({ client, currentUser: mechanic, selectedIssue: '', initialPath: '/work?view=mine' })
  expect(await screen.findByRole('heading', { name: 'Мои задачи' })).toBeVisible()
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-OWNED/ })).toBeVisible()
  expect(screen.queryByRole('button', { name: /Открыть задачу ROBOPARK-QUEUE/ })).not.toBeInTheDocument()
  expect(trackerIssues.mock.calls.every(([query]) => query.owned_by_me)).toBe(true)
})

it('shows assigned pending reviews as operator my tasks', async () => {
  const assignedReview = {
    ...issue,
    key: 'ROBOPARK-REVIEW',
    summary: 'Ждёт проверки оператора',
    workflow: { ...reviewWorkflowIssue.workflow!, review_state: 'pending' as const },
  }
  const trackerIssues = vi.fn(async query => page(query.owned_by_me ? [assignedReview] : [issue]))
  renderWorkbench({
    client: apiClient({ trackerIssues }), currentUser: user, selectedIssue: '',
    initialPath: '/work?view=mine',
  })

  expect(await screen.findByRole('heading', { name: 'Ждут проверки' })).toBeVisible()
  expect(screen.getByRole('button', { name: /Открыть задачу ROBOPARK-REVIEW/ })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Очередь' })).toBeVisible()
  expect(trackerIssues).toHaveBeenCalledWith(expect.objectContaining({ owned_by_me: true }))
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

  expect(searchInventory).toHaveBeenCalledWith({ parkId: 7, query: '', limit: 200, offset: 0 })
  expect(searchInventory).not.toHaveBeenCalledWith(expect.objectContaining({ parkId: 8 }))
  await waitFor(() => expect(writeoffInventoryForTask).toHaveBeenCalledWith(issue.key, -91, '1', expect.any(String)))
})

it('announces a write-off and collapses the form after success', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const claimedIssue = {
    ...issue,
    claim: { park_id: park.id },
    assignee: { display: 'mech', login: 'mech' },
    workflow: {
      owner: { display: 'mech', login: 'mech' },
      review_state: null,
      display_status: 'in_progress' as const,
      sync_state: 'saved' as const,
      has_current_cycle_comment: false,
    },
  }
  const part: InventoryCatalogSearchItem = {
    id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91',
    is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1',
    location: 'Склад парка', stock_is_active: true,
  }
  const client = apiClient({
    trackerIssue: vi.fn(async () => claimedIssue),
    searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })),
    writeoffInventoryForTask: vi.fn(async () => ({ id: 1 } as Awaited<ReturnType<typeof api.writeoffInventoryForTask>>)),
  })

  renderWorkbench({ client, currentUser: mechanic })
  fireEvent.click(await screen.findByRole('button', { name: 'Списать запчасть' }))
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  expect(await screen.findByText('Запчасть списана', { selector: '[role="status"]' })).toBeVisible()
  expect(screen.queryByRole('form', { name: 'Списание запчасти' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Списать запчасть' }))
  expect(screen.queryByText('Запчасть списана', { selector: '[role="status"]' })).not.toBeInTheDocument()
  expect(await screen.findByRole('form', { name: 'Списание запчасти' })).toBeVisible()
})

it('keeps an offline write-off visible as pending when sync later needs attention', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const claimedIssue = {
    ...issue,
    claim: { park_id: park.id },
    assignee: { display: 'mech', login: 'mech' },
    workflow: {
      owner: { display: 'mech', login: 'mech' }, review_state: null,
      display_status: 'in_progress' as const, sync_state: 'saved' as const,
      has_current_cycle_comment: false,
    },
  }
  const part: InventoryCatalogSearchItem = {
    id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91',
    is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1',
    location: 'Склад парка', stock_is_active: true,
  }
  const enqueueAction = vi.fn(async () => undefined)
  const sync: SyncContextValue = {
    state: { status: 'attention', pending: 1, conflicts: 1 }, enqueueAction,
    enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(),
    findAction: vi.fn(async () => undefined), subscribeAction: vi.fn(() => () => undefined),
  }
  renderWorkbench({
    client: apiClient({
      trackerIssue: vi.fn(async () => claimedIssue),
      searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })),
    }),
    currentUser: mechanic,
    sync,
  })
  fireEvent.click(await screen.findByRole('button', { name: 'Списать запчасть' }))
  await screen.findByRole('option', { name: 'Колёса' })
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(await screen.findByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  await waitFor(() => expect(enqueueAction).toHaveBeenCalledOnce())
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  expect(screen.queryByText('Списание ожидает синхронизации')).not.toBeInTheDocument()
  expect(screen.queryByText('Запчасть списана', { selector: '[role="status"]' })).not.toBeInTheDocument()
  expect(screen.getByRole('form', { name: 'Списание запчасти' })).toBeVisible()
})

it('keeps one queued write-off across close and reopen, then collapses only after confirmation', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const claimedIssue = { ...issue, claim: { park_id: park.id }, assignee: { display: 'mech', login: 'mech' }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false } }
  const part: InventoryCatalogSearchItem = { id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91', is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1', location: 'Склад', stock_is_active: true }
  let current: OfflineAction | undefined
  let listener: ((action: OfflineAction | undefined) => void) | undefined
  const enqueueAction = vi.fn(async input => {
    current = { ...input, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 }
    listener?.(current)
    return current
  })
  const syncEngine: SyncEngineLike = {
    start: vi.fn(), dispose: vi.fn(), subscribe: vi.fn(() => () => undefined),
    getState: () => ({ status: 'idle', pending: current && current.state !== 'confirmed' ? 1 : 0, conflicts: 0 }),
    enqueueAction,
    findAction: vi.fn(async () => current),
    subscribeAction: vi.fn((_id, next) => { listener = next; if (current) next(current); return () => { listener = undefined } }),
  }
  renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => claimedIssue), searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })) }), currentUser: mechanic, syncEngine })
  fireEvent.click(await screen.findByRole('button', { name: 'Списать запчасть' }))
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(enqueueAction).toHaveBeenCalledOnce())
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()

  fireEvent.click(screen.getByRole('button', { name: 'Списать запчасть' }))
  fireEvent.click(screen.getByRole('button', { name: 'Списать запчасть' }))
  expect(await screen.findByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  expect(enqueueAction).toHaveBeenCalledOnce()

  current = { ...current!, state: 'confirmed', updatedAt: 2 }
  act(() => listener?.(current))
  expect(await screen.findByText('Запчасть списана', { selector: '[role="status"]' })).toBeVisible()
  expect(screen.queryByRole('form', { name: 'Списание запчасти' })).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Списать запчасть' }))
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))

  await waitFor(() => expect(enqueueAction).toHaveBeenCalledTimes(2))
  expect(enqueueAction.mock.calls[1][0].id).not.toBe(enqueueAction.mock.calls[0][0].id)
})

it('blocks an early write-off until durable pending-action hydration completes', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const claimedIssue = { ...issue, claim: { park_id: park.id }, assignee: { display: 'mech', login: 'mech' }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false } }
  const part: InventoryCatalogSearchItem = { id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91', is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1', location: 'Склад', stock_is_active: true }
  const existing: OfflineAction = { id: 'existing-writeoff', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: issue.key, action: 'inventory_writeoff', idempotencyKey: 'existing-writeoff', baseRevision: null, dependencies: [], payload: { part_id: -91, quantity: '1', park_id: park.id }, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 }
  let finishHydration!: (action: OfflineAction | undefined) => void
  const findAction = vi.fn((_issueKey: string, action: string) => action === 'claim'
    ? Promise.resolve(undefined)
    : new Promise<OfflineAction | undefined>(resolve => { finishHydration = resolve }))
  const enqueueAction = vi.fn()
  const sync: SyncContextValue = { state: { status: 'idle', pending: 1, conflicts: 0 }, enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(), findAction, subscribeAction: vi.fn(() => () => undefined) }
  renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => claimedIssue), searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })) }), currentUser: mechanic, sync })
  fireEvent.click(await screen.findByRole('button', { name: 'Списать запчасть' }))
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })

  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  fireEvent.submit(screen.getByRole('form', { name: 'Списание запчасти' }))
  expect(enqueueAction).not.toHaveBeenCalled()

  act(() => finishHydration(existing))
  await waitFor(() => expect(findAction).toHaveBeenCalledWith(issue.key, 'inventory_writeoff'))
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  expect(screen.queryByText('Списание ожидает синхронизации')).not.toBeInTheDocument()
  expect(enqueueAction).not.toHaveBeenCalled()
})

it('fails closed when durable write-off hydration fails and retries the original action', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const claimedIssue = { ...issue, claim: { park_id: park.id }, assignee: { display: 'mech', login: 'mech' }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false } }
  const part: InventoryCatalogSearchItem = { id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91', is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1', location: 'Склад', stock_is_active: true }
  const existing: OfflineAction = { id: 'existing-writeoff', deviceId: 'phone', resourceType: 'tracker_issue', resourceId: issue.key, action: 'inventory_writeoff', idempotencyKey: 'existing-writeoff', baseRevision: null, dependencies: [], payload: { part_id: -91, quantity: '1', park_id: park.id }, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 }
  const findWriteoff = vi.fn().mockRejectedValueOnce(new Error('indexeddb unavailable')).mockResolvedValue(existing)
  const findAction = vi.fn((_issueKey: string, action: string) => action === 'claim'
    ? Promise.resolve(undefined)
    : findWriteoff())
  const enqueueAction = vi.fn()
  const sync: SyncContextValue = { state: { status: 'idle', pending: 1, conflicts: 0 }, enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(), findAction, subscribeAction: vi.fn(() => () => undefined) }
  renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => claimedIssue), searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })) }), currentUser: mechanic, sync })
  fireEvent.click(await screen.findByRole('button', { name: 'Списать запчасть' }))
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })

  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось проверить ожидающее списание')
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  fireEvent.submit(screen.getByRole('form', { name: 'Списание запчасти' }))
  expect(enqueueAction).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await waitFor(() => expect(findWriteoff).toHaveBeenCalledTimes(2))
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  expect(screen.queryByText('Списание ожидает синхронизации')).not.toBeInTheDocument()
  expect(enqueueAction).not.toHaveBeenCalled()
})

it('clears a confirmed legacy desktop write-off while mounted and creates a fresh second action', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const legacyIssue = { ...issue, claim: { park_id: park.id }, assignee: { display: 'mech', login: 'mech' }, workflow: undefined }
  const part: InventoryCatalogSearchItem = { id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91', is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1', location: 'Склад', stock_is_active: true }
  let current: OfflineAction | undefined
  let listener: ((action: OfflineAction | undefined) => void) | undefined
  const enqueueAction = vi.fn(async input => (current = { ...input, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 }))
  const subscribeAction = vi.fn((_id: string, next: (action: OfflineAction | undefined) => void) => { listener = next; return () => undefined })
  const sync: SyncContextValue = { state: { status: 'idle', pending: 0, conflicts: 0 }, enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(), findAction: vi.fn(async () => current), subscribeAction }
  renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => legacyIssue), searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })) }), currentUser: mechanic, sync })
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(enqueueAction).toHaveBeenCalledOnce())
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  await waitFor(() => expect(subscribeAction).toHaveBeenCalledOnce())
  const firstId = enqueueAction.mock.calls[0][0].id

  current = { ...current!, state: 'confirmed', updatedAt: 2 }
  act(() => listener?.(current))
  expect(await screen.findByText('Запчасть списана', { selector: '[role="status"]' })).toBeVisible()
  expect(screen.queryByText('Списание ожидает синхронизации')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Списать в задачу' })).toBeEnabled()

  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(enqueueAction).toHaveBeenCalledTimes(2))
  expect(enqueueAction.mock.calls[1][0].id).not.toBe(firstId)
})

it('shows legacy shift handoff under one disclosure without a blank nested summary', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const legacyIssue = { ...issue, claim: { park_id: park.id }, assignee: { display: 'mech', login: 'mech' }, workflow: undefined }
  vi.spyOn(collaborationClient, 'handoff').mockResolvedValue({ revision: 0, done: '', remaining: '', obstacles: '', author: null, updated_at: null })
  const view = renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => legacyIssue) }), currentUser: mechanic })

  await screen.findByRole('heading', { name: legacyIssue.summary })
  expect(view.container.querySelector('.issue-collaboration')).not.toBeNull()
  expect(view.container.querySelector('.issue-collaboration details')).toBeNull()
  expect(screen.getByRole('button', { name: 'Передача смены' })).toBeVisible()
})

it('keeps legacy phone write-off durable across close and confirms through the shared contract', async () => {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const legacyIssue = { ...issue, claim: { park_id: park.id }, assignee: { display: 'mech', login: 'mech' }, workflow: undefined }
  const part: InventoryCatalogSearchItem = { id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91', is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1', location: 'Склад', stock_is_active: true }
  let current: OfflineAction | undefined
  let listener: ((action: OfflineAction | undefined) => void) | undefined
  const enqueueAction = vi.fn(async input => (current = { ...input, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 }))
  const sync: SyncContextValue = { state: { status: 'idle', pending: 0, conflicts: 0 }, enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(), findAction: vi.fn(async () => current), subscribeAction: vi.fn((_id, next) => { listener = next; return () => undefined }) }
  renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => legacyIssue), searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })) }), currentUser: mechanic, sync })
  fireEvent.click(await screen.findByRole('button', { name: 'Использовать запчасть' }))
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(sync.subscribeAction).toHaveBeenCalled())
  fireEvent.click(screen.getByRole('button', { name: 'Использовать запчасть' }))
  fireEvent.click(screen.getByRole('button', { name: 'Использовать запчасть' }))
  expect(await screen.findByRole('button', { name: 'Списать в задачу' })).toBeDisabled()
  expect(enqueueAction).toHaveBeenCalledOnce()

  current = { ...current!, state: 'confirmed', updatedAt: 2 }
  act(() => listener?.(current))
  expect(await screen.findByText('Запчасть списана', { selector: '[role="status"]' })).toBeVisible()
  expect(screen.queryByRole('form', { name: 'Списание запчасти' })).not.toBeInTheDocument()
})

it('retires a terminal claim conflict and confirms an explicit retry with a fresh identity', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', parks: [park] }
  const claimedIssue = { ...issue, claim: { park_id: park.id }, assignee: { display: 'mech', login: 'mech' }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false } }
  const part: InventoryCatalogSearchItem = { id: 91, component_id: 21, component_name: 'Колёса', name: 'Шина', article: 'WH-91', is_active: true, has_photo: false, quantity: '3', minimum_quantity: '1', location: 'Склад', stock_is_active: true }
  let current: OfflineAction | undefined
  let listener: ((action: OfflineAction | undefined) => void) | undefined
  const enqueueAction = vi.fn(async input => (current = { ...input, state: 'ready', attempts: 0, createdAt: 1, updatedAt: Date.now() }))
  const cancelAction = vi.fn(async () => undefined)
  const sync: SyncContextValue = { state: { status: 'idle', pending: 0, conflicts: 0 }, enqueueAction, enqueueMedia: vi.fn(), syncNow: vi.fn(), cancelAction, resolveConflict: vi.fn(), findAction: vi.fn(async () => current), subscribeAction: vi.fn((_id, next) => { listener = next; return () => undefined }) }
  renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => claimedIssue), searchInventory: vi.fn(async () => ({ items: [part], limit: 200, offset: 0, total: 1 })) }), currentUser: mechanic, sync })
  fireEvent.click(await screen.findByRole('button', { name: 'Списать запчасть' }))
  fireEvent.change(await screen.findByRole('combobox', { name: 'Компонента' }), { target: { value: '21' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Запчасть' }), { target: { value: '91' } })
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(enqueueAction).toHaveBeenCalledOnce())
  await waitFor(() => expect(sync.subscribeAction).toHaveBeenCalled())
  const firstId = enqueueAction.mock.calls[0][0].id
  current = { ...current!, state: 'conflict', updatedAt: 2 }
  act(() => listener?.(current))

  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось синхронизировать списание')
  expect(screen.queryByText('Запчасть списана')).not.toBeInTheDocument()
  expect(enqueueAction).toHaveBeenCalledOnce()
  fireEvent.click(screen.getByRole('button', { name: 'Списать в задачу' }))
  await waitFor(() => expect(enqueueAction).toHaveBeenCalledTimes(2))
  const retryId = enqueueAction.mock.calls[1][0].id
  expect(cancelAction).toHaveBeenCalledWith(firstId)
  expect(retryId).not.toBe(firstId)
  expect(enqueueAction.mock.calls[1][0].idempotencyKey).toBe(retryId)

  current = { ...current!, state: 'confirmed', updatedAt: 3 }
  act(() => listener?.(current))
  expect(await screen.findByText('Запчасть списана', { selector: '[role="status"]' })).toBeVisible()
  expect(screen.queryByRole('form', { name: 'Списание запчасти' })).not.toBeInTheDocument()
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

  const open = await screen.findByRole('button', {
    name: `Открыть задачу ${issue.key}: ${issue.summary}`,
  })
  fireEvent.click(open)
  fireEvent.click(await screen.findByRole('button', { name: 'Взять вместо сменщика' }))
  await waitFor(() => expect(client.trackerAssign).toHaveBeenCalledWith(issue.key, 'mech2'))
})

it('renders a duplicate upstream task key only once', async () => {
  const client = apiClient({ trackerIssues: vi.fn(async () => page([issue, issue])) })
  renderWorkbench({ client, selectedIssue: '' })
  expect(await screen.findAllByRole('button', {
    name: `Открыть задачу ${issue.key}: ${issue.summary}`,
  })).toHaveLength(1)
})

it('shows only attention tasks for the shareable sync attention state', async () => {
  const attention = {
    ...issue, key: 'ROBOPARK-ATTENTION', summary: 'Конфликт синхронизации',
    workflow: { owner: null, review_state: null, display_status: 'queued' as const, sync_state: 'needs_attention' as const, has_current_cycle_comment: false },
  }
  const saved = {
    ...issue, key: 'ROBOPARK-SAVED', summary: 'Обычная задача',
    workflow: { owner: null, review_state: null, display_status: 'queued' as const, sync_state: 'saved' as const, has_current_cycle_comment: false },
  }
  const trackerIssues = vi.fn(async query => page(query.sync_state === 'needs_attention' ? [attention] : [attention, saved]))
  renderWorkbench({
    client: apiClient({ trackerIssues }),
    selectedIssue: '', currentState: { ...state, sync: 'needs_attention' },
  })
  expect(await screen.findByText(/Показаны только задачи, требующие внимания/)).toBeVisible()
  expect(screen.getByRole('button', { name: /ROBOPARK-ATTENTION/ })).toBeVisible()
  expect(screen.queryByRole('button', { name: /ROBOPARK-SAVED/ })).not.toBeInTheDocument()
  expect(trackerIssues).toHaveBeenCalledWith(expect.objectContaining({ sync_state: 'needs_attention' }))
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
  initialPath = '/',
  sync,
  syncEngine,
  now,
}: {
  client?: IssueWorkbenchApiClient
  selectedIssue?: string
  currentState?: WorkUrlState
  currentUser?: User
  selectedPark?: Park
  onAuthorizationFailure?: () => Promise<unknown>
  strictMode?: boolean
  initialPath?: string
  sync?: SyncContextValue
  syncEngine?: SyncEngineLike
  now?: number
} = {}) {
  const modeStore = createInterfaceModeStore(() => ({
    getItem: () => null,
    removeItem: () => undefined,
  }))
  const onStateChange = vi.fn()
  const onOpenIssue = vi.fn()
  const onCloseIssue = vi.fn()
  function ControlledWorkbench() {
    const [value, setValue] = useState(currentState)
    return <IssueWorkbench apiClient={client} issueKey={selectedIssue}
      now={now}
      onAuthorizationFailure={onAuthorizationFailure} onCloseIssue={onCloseIssue} onOpenIssue={onOpenIssue}
      onStateChange={(next, options) => { onStateChange(next, options); setValue(next) }}
      selectedPark={selectedPark} state={value} user={currentUser} />
  }
  const auth = { user: currentUser, loading: false, login: vi.fn(), logout: vi.fn(), refreshUser: vi.fn() }
  const parkContext = { parkId: selectedPark.id, selectedPark, parks: currentUser.parks, loading: false, locked: false, setParkId: vi.fn(), refreshParks: vi.fn() }
  const content = syncEngine
    ? <AuthContext.Provider value={auth}><ParkScopeContext.Provider value={parkContext}><SyncProvider engineFactory={async () => syncEngine}><ControlledWorkbench /></SyncProvider></ParkScopeContext.Provider></AuthContext.Provider>
    : sync
      ? <SyncContextProvider value={sync}><ControlledWorkbench /></SyncContextProvider>
      : <ControlledWorkbench />
  const view = render(content, {
    wrapper: ({ children }) => <InterfaceModeProvider accountId={currentUser.id} store={modeStore}><MemoryRouter initialEntries={[initialPath]}>{children}</MemoryRouter></InterfaceModeProvider>,
    reactStrictMode: strictMode,
  })
  return {
    ...view,
    onAuthorizationFailure,
    onCloseIssue,
    onOpenIssue,
    onStateChange,
  }
}

async function openTaskChat() {
  fireEvent.click(await screen.findByRole('button', { name: 'История и сообщения' }))
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
    [...currentUser.parks].sort((a, b) => a.id - b.id).map(scope),
  ])}:${JSON.stringify(scope(currentPark))}:`
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
  it('uses the first queue park timezone for SLA after a task moves to another park', async () => {
    const transferred: TrackerIssueDetail = {
      ...queuedWorkflowIssue,
      queued_at: '2026-09-18T12:00:00Z',
      sla_deadline: '2026-09-18T17:00:00Z',
      sla_source: 'status_history',
      sla_timezone: 'Europe/Moscow',
    }
    renderWorkbench({
      now: Date.parse('2026-09-18T14:00:00Z'),
      selectedPark: { ...park, timezone: 'Asia/Yekaterinburg' },
      client: apiClient({ trackerIssues: vi.fn(async () => page([transferred])), trackerIssue: vi.fn(async () => transferred) }),
    })
    expect((await screen.findAllByText('SLA: 3:00')).length).toBeGreaterThan(0)
  })

  it('prioritizes the remaining SLA instead of calendar downtime or ticket creation age', async () => {
    const timed = {
      ...queuedWorkflowIssue,
      created_at: '2026-09-01T00:00:00Z',
      hours_created: '60',
      queued_at: '2026-09-03T00:00:00Z',
      sla_deadline: '2026-09-03T12:00:00Z',
      sla_source: 'status_history' as const,
    }
    renderWorkbench({
      now: Date.parse('2026-09-03T12:00:00Z'),
      client: apiClient({ trackerIssues: vi.fn(async () => page([timed])), trackerIssue: vi.fn(async () => timed) }),
    })
    await screen.findByRole('heading', { name: issue.summary })
    expect(screen.getAllByText(/SLA: 0:00/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Простой/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Возраст: 60/)).not.toBeInTheDocument()
  })
  it('keeps a task read-only when the server has not supplied workflow state', async () => {
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => issue) }) })
    expect(await screen.findByRole('heading', { name: issue.summary })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Статус задачи' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Исполнитель' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: ru.tracker.actions.close })).not.toBeInTheDocument()
    const degraded = screen.getByText(/Актуальное состояние задачи пока недоступно/).closest('.panel-hint') as HTMLElement
    expect(degraded).toBeVisible()
    expect(within(degraded).getByRole('button', { name: 'Повторить загрузку' })).toBeVisible()
  })

  it('uses workflow owner and server comment eligibility as authoritative state', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = {
      ...issue, assignee: { display: 'stale', login: 'stale' }, claim: { park_id: park.id },
      workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: true },
    }
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => workflowIssue) }) })
    expect(await screen.findByRole('button', { name: 'Передать на проверку' })).toBeVisible()
    fireEvent.click(screen.getAllByRole('button', { name: 'Передать на проверку' }).at(-1)!)
    expect(await screen.findByRole('textbox', { name: 'Добавить уточнение' })).not.toBeRequired()
  })

  it('stores exactly one review photo locally before queueing the review', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = {
      ...issue, claim: { park_id: park.id },
      workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: true },
    }
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:review')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    const enqueueMedia = vi.fn(async () => undefined)
    const enqueueAction = vi.fn(async () => undefined)
    const sync = {
      state: { status: 'idle' as const, pending: 0, conflicts: 0 },
      enqueueMedia,
      enqueueAction,
      syncNow: vi.fn(async () => false),
      cancelAction: vi.fn(async () => undefined),
      resolveConflict: vi.fn(async () => undefined),
      findAction: vi.fn(async () => undefined),
      subscribeAction: vi.fn(() => () => undefined),
    } satisfies SyncContextValue
    const taskSubmitReview = vi.fn()
    renderWorkbench({
      currentUser: mechanic,
      client: apiClient({
        trackerIssue: vi.fn(async () => workflowIssue), taskSubmitReview,
        taskDefectCodes: vi.fn(async () => [{ code: 'BD-01', label: 'Вмятина', description: null }]),
      }),
      sync,
    })
    await screen.findByRole('button', { name: 'Передать на проверку' })
    fireEvent.click(screen.getAllByRole('button', { name: 'Передать на проверку' }).at(-1)!)
    fireEvent.change(screen.getByLabelText('Код дефекта'), { target: { value: 'BD-01' } })
    const photo = new File(['photo'], 'robot.jpg', { type: 'image/jpeg' })
    fireEvent.change(screen.getByLabelText('Сделать фото или выбрать файл'), { target: { files: [photo] } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Передать на проверку' }).at(-1)!)

    await waitFor(() => expect(enqueueMedia).toHaveBeenCalledOnce())
    expect(enqueueMedia).toHaveBeenCalledWith(
      expect.objectContaining({ issueKey: issue.key, name: 'robot.jpg', blob: expect.any(Blob) }),
      expect.objectContaining({ action: 'submit_review', dependencies: [expect.stringMatching(/^media-/)] }),
    )
    expect(enqueueAction).not.toHaveBeenCalled()
    expect(taskSubmitReview).not.toHaveBeenCalled()
  })

  it('uploads an ordinary photo as one workflow chat message without closing the task', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = { ...issue, claim: { park_id: park.id }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: true } }
    const taskMessage = vi.fn(async (_key: string, text: string) => taskMessageResult(text))
    const taskPhoto = vi.fn(async () => ({ id: 'photo-1', message_id: 'message-1', name: 'robot.jpg', mimetype: 'image/jpeg', size: 5, sha256: 'abc', action_id: 'action-1', sync_state: 'pending' as const }))
    const trackerAttach = vi.fn(async () => actionResult('attach'))
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => workflowIssue), taskMessage, taskPhoto, trackerAttach } as Partial<IssueWorkbenchApiClient>) })

    await openTaskChat()
    expect(await screen.findByText(ru.tracker.attachPhoto)).toBeVisible()
    const photo = new File(['image'], 'robot.jpg', { type: 'image/jpeg' })
    fireEvent.change(document.querySelector('.issue-attach-group input[type="file"]')!, { target: { files: [photo] } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.attachPhotoSubmit }))

    await waitFor(() => expect(taskPhoto).toHaveBeenCalledOnce())
    expect(taskPhoto).toHaveBeenCalledWith(issue.key, photo, expect.any(String))
    expect(taskMessage).not.toHaveBeenCalled()
    expect(trackerAttach).not.toHaveBeenCalled()
  })

  it('retries an ordinary photo with the same idempotency key', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = { ...issue, claim: { park_id: park.id }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: true } }
    const taskMessage = vi.fn(async (_key: string, text: string) => taskMessageResult(text))
    const taskPhoto = vi.fn().mockRejectedValueOnce(new Error('network lost')).mockResolvedValueOnce({ id: 'photo-1' })
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => workflowIssue), taskMessage, taskPhoto } as Partial<IssueWorkbenchApiClient>) })

    await openTaskChat()
    await screen.findByText(ru.tracker.attachPhoto)
    fireEvent.change(document.querySelector('.issue-attach-group input[type="file"]')!, { target: { files: [new File(['image'], 'robot.jpg', { type: 'image/jpeg' })] } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.attachPhotoSubmit }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.attachPhotoSubmit }))
    await waitFor(() => expect(taskPhoto).toHaveBeenCalledTimes(2))

    expect(taskMessage).not.toHaveBeenCalled()
    expect(taskPhoto.mock.calls[0]?.[2]).toBe(taskPhoto.mock.calls[1]?.[2])
  })

  it('retries a locally committed message with the same idempotency key', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = { ...issue, claim: { park_id: park.id }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'pending', has_current_cycle_comment: false } }
    const taskMessage = vi.fn().mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({})
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => workflowIssue), taskMessage }) })
    await openTaskChat()
    const composer = await screen.findByRole('textbox', { name: ru.tracker.comments })
    fireEvent.change(composer, { target: { value: 'Заменил датчик' } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await screen.findByRole('alert')
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
    await waitFor(() => expect(taskMessage).toHaveBeenCalledTimes(2))
    expect(taskMessage.mock.calls[0]?.[2]).toBe(taskMessage.mock.calls[1]?.[2])
  })

  it('shows an offline comment immediately and does not wait for Tracker', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const workflowIssue: TrackerIssueDetail = { ...issue, claim: { park_id: park.id }, workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress', sync_state: 'saved', has_current_cycle_comment: false } }
    const taskMessage = vi.fn()
    const enqueueAction = vi.fn(async (_input: Parameters<SyncContextValue['enqueueAction']>[0]) => undefined)
    let actionListener: ((action: OfflineAction | undefined) => void) | undefined
    const sync = {
      state: { status: 'idle' as const, pending: 0, conflicts: 0 },
      enqueueMedia: vi.fn(async () => undefined),
      enqueueAction,
      syncNow: vi.fn(async () => false),
      cancelAction: vi.fn(async () => undefined),
      resolveConflict: vi.fn(async () => undefined),
      findAction: vi.fn(async () => undefined),
      subscribeAction: vi.fn((_id, listener) => { actionListener = listener; return () => { actionListener = undefined } }),
    } satisfies SyncContextValue
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => workflowIssue), taskMessage }), sync })
    await openTaskChat()
    const composer = await screen.findByRole('textbox', { name: ru.tracker.comments })
    fireEvent.change(composer, { target: { value: 'Заменил датчик офлайн' } })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))

    await waitFor(() => expect(enqueueAction).toHaveBeenCalledOnce())
    expect(enqueueAction).toHaveBeenCalledWith(expect.objectContaining({
      action: 'comment', resourceId: issue.key, payload: { text: 'Заменил датчик офлайн', park_id: park.id },
    }))
    expect(taskMessage).not.toHaveBeenCalled()
    expect(await screen.findByText('Заменил датчик офлайн')).toBeVisible()
    const queued = enqueueAction.mock.calls[0][0]
    await act(async () => { actionListener?.({ ...queued, state: 'conflict', attempts: 1, createdAt: 1, updatedAt: 2 }) })
    await waitFor(() => expect(screen.queryByText('Заменил датчик офлайн')).not.toBeInTheDocument())
    expect(resourceStore.get<TaskTimelineItem[]>(`${accessPrefix(mechanic)}comments:${issue.key}`)?.some(item => item.id === queued.id)).toBe(false)
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
  it('distinguishes returned work from the Tracker queue status', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    const returnedIssue: TrackerIssueDetail = {
      ...issue,
      status: 'В очереди',
      status_key: 'queued',
      claim: { park_id: park.id },
      workflow: {
        owner: { display: 'mech', login: 'mech' },
        review_state: 'returned',
        display_status: 'in_progress',
        sync_state: 'saved',
        has_current_cycle_comment: true,
      },
    }
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => returnedIssue) }) })

    expect(await screen.findByRole('heading', { name: issue.summary })).toBeVisible()
    expect(screen.getByText('На доработке')).toBeVisible()
    expect(screen.getByText('В Tracker: В очереди')).toBeVisible()
    expect(screen.queryByText('В работе')).not.toBeInTheDocument()
  })
  it('shows the Tracker status before a task enters the local repair workflow', async () => {
    const waitingIssue: TrackerIssueDetail = {
      ...issue,
      status: 'Ожидание поставки',
      status_key: 'deliveryWaiting',
      assignee: null,
      claim: null,
      workflow: {
        owner: null,
        review_state: null,
        display_status: 'queued',
        sync_state: 'saved',
        has_current_cycle_comment: false,
      },
    }
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => waitingIssue) }) })

    const card = (await screen.findByRole('heading', { name: issue.summary })).closest('article')
    expect(card).not.toBeNull()
    expect(within(card!).getByText('Ожидание поставки')).toBeVisible()
    expect(within(card!).queryByText('В очереди')).not.toBeInTheDocument()
  })
  it('uses one chat and keeps task actions in compact lifecycle order', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => ({
      ...issue, assignee: { display: 'mech', login: 'mech' }, claim: { park_id: park.id },
      workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false },
    })) }) })
    expect(await screen.findByRole('heading', { name: issue.summary })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'История действий' })).not.toBeInTheDocument()
    const parts = screen.getByRole('button', { name: 'Списать запчасть' })
    const handoff = screen.getByRole('button', { name: 'Передать смену' })
    expect(parts).toHaveAttribute('aria-expanded', 'false')
    expect(handoff).toHaveAttribute('aria-expanded', 'false')
    expect(parts.compareDocumentPosition(handoff) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Статус задачи' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: ru.tracker.actions.openInTracker })).not.toBeInTheDocument()
  })
  it('uses one tab system and aligned disclosure actions', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    renderWorkbench({ currentUser: mechanic, client: apiClient({ trackerIssue: vi.fn(async () => ({
      ...issue, assignee: { display: 'mech', login: 'mech' }, claim: { park_id: park.id },
      workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false },
    })) }) })

    const primaryTabs = await screen.findByRole('tablist', { name: 'Разделы задачи' })
    expect(primaryTabs).toBeVisible()
    expect(primaryTabs).toHaveClass('rp-tabs--plain')
    expect(within(primaryTabs).getByRole('tab', { name: 'Задача' })).toBeVisible()
    expect(within(primaryTabs).getByRole('tab', { name: 'Проверка' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'История и сообщения' })).toBeVisible()
    expect(screen.queryByRole('tablist', { name: 'Другие задачи робота' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Списать запчасть' })).toHaveClass('rp-disclosure-action')
    expect(screen.getByRole('button', { name: 'Передать смену' })).toHaveClass('rp-disclosure-action')
    const disclosures = screen.getByRole('group', { name: 'Дополнительные разделы задачи' })
    expect(disclosures).not.toHaveClass('rp-responsive-disclosure-group')
    const style = document.createElement('style')
    style.textContent = readFileSync('src/components/tracker/task-card.css', 'utf8')
    document.head.append(style)
    expect(getComputedStyle(disclosures).display).toBe('flex')
    expect(getComputedStyle(disclosures).flexWrap).toBe('wrap')
    const phoneRules = Array.from(style.sheet!.cssRules)
      .filter((rule): rule is CSSMediaRule => 'conditionText' in rule && rule.conditionText === '(max-width: 599px)')
      .flatMap(rule => Array.from(rule.cssRules))
    const phoneDisclosure = Array.from(phoneRules).find(
      (rule): rule is CSSStyleRule => 'selectorText' in rule && rule.selectorText === '.rp-task-disclosure-actions',
    )
    expect(phoneDisclosure?.style.display).toBe('grid')
    expect(phoneDisclosure?.style.gap).toBe('12px')
    expect(phoneDisclosure?.style.gridTemplateColumns).toBe('repeat(2, minmax(0, 1fr))')
    style.remove()
  })
  it('keeps history inside the task and preserves the message draft when collapsed', async () => {
    const mechanic = { ...user, username: 'mech', role: 'mechanic' as const }
    renderWorkbench({ currentUser: mechanic, client: apiClient({
      trackerIssue: vi.fn(async () => ({
        ...issue, assignee: { display: 'mech', login: 'mech' }, claim: { park_id: park.id },
        workflow: { owner: { display: 'mech', login: 'mech' }, review_state: null, display_status: 'in_progress' as const, sync_state: 'saved' as const, has_current_cycle_comment: false },
      })),
      taskTimeline: vi.fn(async () => [taskMessageResult('Проверил привод')]),
    }) })

    const parts = await screen.findByRole('button', { name: 'Списать запчасть' })
    expect(screen.getByRole('button', { name: 'Передать на проверку' })).toBeVisible()
    fireEvent.click(parts)
    expect(parts).toHaveAttribute('aria-expanded', 'true')
    expect(screen.queryByText('Проверил привод')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: ru.tracker.comments })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'История и сообщения' }))
    expect(await screen.findByText('Проверил привод')).toBeVisible()
    const composer = screen.getByRole('textbox', { name: ru.tracker.comments })
    fireEvent.change(composer, { target: { value: 'Черновик ответа' } })
    expect(screen.getByRole('button', { name: 'Списать запчасть' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Передать на проверку' })).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'История и сообщения' }))
    expect(screen.getByRole('button', { name: 'Списать запчасть' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Передать на проверку' })).toBeVisible()
    expect(screen.queryByRole('textbox', { name: ru.tracker.comments })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'История и сообщения' }))
    expect(screen.getByRole('textbox', { name: ru.tracker.comments })).toHaveValue('Черновик ответа')
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
    expect(screen.queryByRole('tab', { name: 'Проверка' })).not.toBeInTheDocument()
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
    expect(screen.queryByRole('tabpanel', { name: 'Проверка' })).not.toBeInTheDocument()
    expect(emergencyResolve).not.toHaveBeenCalled()
  })

  it('keeps phone workflow actions and timeline inside the task card', async () => {
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    const mechanic = { ...user, role: 'mechanic' as const }
    const currentIssue = { ...queuedWorkflowIssue, assignee: { display: 'Operator', login: 'operator' },
      workflow: { ...queuedWorkflowIssue.workflow!, owner: { display: 'Operator', login: 'operator' } } }
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
    expect(screen.getAllByText('В очереди').some(element => element.closest('.issue-detail'))).toBe(true)
    expect(screen.getByText('Operator')).toBeVisible()
    expect(screen.queryByText('Последняя важная деталь')).not.toBeInTheDocument()
    expect(screen.queryByText('Старый комментарий')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: ru.tracker.comments })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: ru.tracker.history })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Статус задачи' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Исполнитель' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Передать смену' })).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(screen.getByRole('button', { name: 'История и сообщения' }))
    expect(screen.getByText('Последняя важная деталь')).toBeVisible()
    expect(screen.getByText('Старый комментарий')).toBeVisible()
    expect(screen.getByRole('textbox', { name: ru.tracker.comments })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'История и сообщения' }))
    expect(document.querySelector('.issue-collaboration')).not.toBeInTheDocument()
    expect(screen.queryByText('Загружаем передачу смены…')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Передать смену' }))
    expect(document.querySelector('.issue-collaboration')).toBeInTheDocument()
  })

  it('keeps the selected issue open without a collapse control', async () => {
    renderWorkbench()

    expect(await screen.findByRole('heading', { name: issue.summary })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Свернуть: Детали задачи' })).not.toBeInTheDocument()
  })

  it('paints settled same-scope detail immediately on route return', async () => {
    const client = apiClient()
    const first = renderWorkbench({ client })
    await screen.findByRole('heading', { name: issue.summary })
    vi.mocked(client.trackerIssue).mockImplementation(() => new Promise(() => undefined))
    first.unmount()
    renderWorkbench({ client })
    expect(screen.getByRole('heading', { name: issue.summary })).toBeInTheDocument()
  })

  it('keeps settled work data across route remount while revalidating once', async () => {
    const client = apiClient()
    const first = renderWorkbench({ client, selectedIssue: '' })
    await screen.findByRole('button', { name: `Открыть задачу ${issue.key}: ${issue.summary}` })
    first.unmount()

    renderWorkbench({ client, selectedIssue: '' })

    expect(screen.getByRole('button', { name: `Открыть задачу ${issue.key}: ${issue.summary}` })).toBeVisible()
    await waitFor(() => expect(client.trackerIssues).toHaveBeenCalledTimes(2))
  })

  it.each(['pending', 'cached'] as const)('retires pending but retains settled %s park data across A, B, A', async (mode) => {
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
    if (mode === 'cached') expect(screen.getByRole('heading', { name: issue.summary })).toBeInTheDocument()
    else expect(screen.queryByRole('heading', { name: issue.summary })).not.toBeInTheDocument()
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

  it('keeps task, check, chat and related repairs in one task navigation', async () => {
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => queuedWorkflowIssue) }) })
    await screen.findByRole('heading', { name: issue.summary })
    const tabs = screen.getByRole('tablist', { name: 'Разделы задачи' })
    expect(within(tabs).getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      'Задача', 'Проверка', 'Открытые', 'Закрытые',
    ])
    expect(screen.getByRole('button', { name: 'История и сообщения' })).toHaveAttribute('aria-expanded', 'false')
    expect(within(tabs).getByRole('tab', { name: 'Открытые задачи' })).toBeVisible()
    expect(within(tabs).getByRole('tab', { name: 'Закрытые задачи' })).toBeVisible()
    expect(screen.queryByRole('tablist', { name: 'Другие задачи робота' })).not.toBeInTheDocument()
  })

  it('marks selected-task mode and omits an origin link back to the same task', async () => {
    renderWorkbench()

    await screen.findByRole('heading', { name: issue.summary })
    expect(document.querySelector('.rp-work-domain')).toHaveAttribute('data-has-detail', 'true')
    expect(screen.queryByRole('navigation', { name: 'Возврат к главному блокеру' })).not.toBeInTheDocument()
  })

  it('keeps the origin link when a related task differs from its main blocker', async () => {
    renderWorkbench({ currentState: { ...state, rootIssue: 'ROBOPARK-1' } })

    await screen.findByRole('heading', { name: issue.summary })
    expect(screen.getByRole('link', { name: 'К главному блокеру ROBOPARK-1' })).toHaveAttribute(
      'href', expect.stringContaining('/work/ROBOPARK-1'),
    )
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

  it('highlights a possible repeat when another task on the robot has the same problem', async () => {
    const client = apiClient({ trackerIssues: vi.fn()
      .mockResolvedValueOnce(page())
      .mockResolvedValueOnce(page([
        issue,
        { ...issue, key: 'ROBOPARK-7', summary: '  РОБОТ не продолжает маршрут!  ' },
        { ...issue, key: 'ROBOPARK-8', summary: 'Не заряжается аккумулятор' },
      ])) })
    renderWorkbench({ client })
    fireEvent.click(await screen.findByRole('tab', { name: 'Открытые задачи' }))
    const repeated = await screen.findByText('Возможный повтор проблемы')
    expect(repeated.closest('.rp-entity-row')).toHaveClass('rp-work-possible-repeat')
    expect(screen.getByText(/Не заряжается аккумулятор/).closest('.rp-entity-row')).not.toHaveClass('rp-work-possible-repeat')
    expect(client.trackerIssues).toHaveBeenCalledTimes(2)
  })

  it('recognizes the same robot problem across different Tracker author suffixes', async () => {
    const current = { ...issue, summary: '[a447] MECHANICAL_PROBLEM by ivan' }
    const client = apiClient({
      trackerIssue: vi.fn(async () => current),
      trackerIssues: vi.fn()
        .mockResolvedValueOnce(page())
        .mockResolvedValueOnce(page([
          { ...issue, key: 'ROBOPARK-7', summary: 'mechanical_problem by petr' },
          { ...issue, key: 'ROBOPARK-8', summary: '[A447] OTHER by petr' },
        ])),
    })
    renderWorkbench({ client })
    fireEvent.click(await screen.findByRole('tab', { name: 'Открытые задачи' }))

    expect((await screen.findByText(/mechanical_problem by petr/i)).closest('.rp-entity-row'))
      .toHaveClass('rp-work-possible-repeat')
    expect(screen.getByText(/OTHER by petr/).closest('.rp-entity-row'))
      .not.toHaveClass('rp-work-possible-repeat')
    expect(client.trackerIssues).toHaveBeenCalledTimes(2)
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
    await screen.findByText('Робот в задаче не указан — связанные задачи недоступны.')
    expect(screen.queryByRole('heading', { name: /Открытые задачи робота/ })).not.toBeInTheDocument()
    expect(client.trackerIssues).toHaveBeenCalledTimes(1)
  })

  it('keeps the selected issue and its capability-gated actions available when related robot work fails', async () => {
    const client = apiClient({
      trackerIssues: vi.fn()
        .mockResolvedValueOnce(page())
        .mockRejectedValueOnce(new ApiError(502, 'tracker_upstream_error'))
        .mockResolvedValueOnce(page()),
      trackerIssue: vi.fn(async () => reviewWorkflowIssue),
    })

    renderWorkbench({ client })

    expect(await screen.findByRole('heading', { name: issue.summary })).toBeVisible()
    expect(await screen.findByRole('button', { name: 'Принять и закрыть' })).toBeEnabled()
    fireEvent.click(screen.getByRole('tab', { name: 'Открытые задачи' }))
    expect(await screen.findByRole('heading', { name: 'Сервис временно недоступен' })).toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: 'Задача' }))
    expect(screen.getByRole('button', { name: 'Принять и закрыть' })).toBeEnabled()
  })

  it('does not offer an empty mechanic handoff form to the reviewing operator', async () => {
    renderWorkbench({ client: apiClient({ trackerIssue: vi.fn(async () => reviewWorkflowIssue) }) })
    expect(await screen.findByRole('button', { name: 'Принять и закрыть' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Передать смену' })).not.toBeInTheDocument()
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
  ] as const)('ignores a pending review approval %s replacement followed by old %s', async (change, result) => {
    const pending = deferred<TaskActionResult>()
    const nextIssue = { ...reviewWorkflowIssue, key: change === 'issue' ? 'ROBOPARK-99' : issue.key, summary: 'Новый открытый экран' }
    const nextPark = change === 'park' ? { ...park, id: 8, tag: 'Beta' } : park
    const nextUser = change === 'principal' ? { ...user, id: 4, username: 'next-operator' } : user
    const client = apiClient({ trackerIssue: vi.fn(async () => reviewWorkflowIssue), taskApproveReview: vi.fn(() => pending.promise) })
    const onCloseIssue = vi.fn()
    const onAuthorizationFailure = vi.fn(async () => undefined)
    const tree = (replacement: boolean) => <Harness><IssueWorkbench apiClient={client}
      user={replacement ? nextUser : user} selectedPark={replacement ? nextPark : park}
      issueKey={replacement ? nextIssue.key : issue.key} state={state}
      onCloseIssue={onCloseIssue} onAuthorizationFailure={onAuthorizationFailure}
      onOpenIssue={vi.fn()} onStateChange={vi.fn()} /></Harness>
    const view = render(tree(false), { reactStrictMode: true })
    await screen.findByRole('heading', { name: issue.summary })
    fireEvent.click(screen.getByRole('button', { name: 'Принять и закрыть' }))
    await waitFor(() => expect(client.taskApproveReview).toHaveBeenCalledWith(issue.key, expect.any(String)))
    vi.mocked(client.trackerIssue).mockResolvedValue(nextIssue)
    view.rerender(tree(true))
    await screen.findByRole('heading', { name: nextIssue.summary })
    const invalidate = vi.spyOn(resourceStore, 'invalidate')
    const readCounts = [vi.mocked(client.trackerIssues).mock.calls.length, vi.mocked(client.trackerIssue).mock.calls.length]
    await act(async () => {
      if (result === 'success') pending.resolve(taskActionResult('approve-review'))
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

  it('still approves and refreshes the current owner after StrictMode re-setup', async () => {
    const pending = deferred<TaskActionResult>()
    const client = apiClient({ trackerIssue: vi.fn(async () => reviewWorkflowIssue), taskApproveReview: vi.fn(() => pending.promise) })
    const { onCloseIssue } = renderWorkbench({ client, strictMode: true })
    await screen.findByRole('heading', { name: issue.summary })
    fireEvent.click(screen.getByRole('button', { name: 'Принять и закрыть' }))
    await waitFor(() => expect(client.taskApproveReview).toHaveBeenCalledOnce())
    await act(async () => { pending.resolve(taskActionResult('approve-review')) })
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

  it('refreshes the owned list and selected issue without clearing visible data', async () => {
    const revalidate = vi.spyOn(resourceStore, 'revalidate')
    const clearAll = vi.spyOn(resourceStore, 'clearAll')
    const client = apiClient({ trackerIssue: vi.fn(async () => queuedWorkflowIssue), taskMessage: vi.fn(async () => taskMessageResult('Новая деталь')) })
    renderWorkbench({ client })

    await screen.findByRole('heading', { name: issue.summary })
    await openTaskChat()
    fireEvent.change(screen.getByLabelText(ru.tracker.comments), {
      target: { value: 'Новая деталь' },
    })
    fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))

    await screen.findByText('Действие выполнено')
    expect(revalidate).toHaveBeenCalledTimes(6)
    expect(revalidate).toHaveBeenCalledWith(`${accessPrefix()}list:${park.id}:`, {
      prefix: true,
    })
    expect(revalidate).toHaveBeenCalledWith(`${accessPrefix()}owned:${user.username}`, { prefix: true })
    expect(revalidate).toHaveBeenCalledWith(`${accessPrefix()}issue:${issue.key}`)
    expect(revalidate).toHaveBeenCalledWith(`${accessPrefix()}comments:${issue.key}`)
    expect(revalidate).toHaveBeenCalledWith(`${accessPrefix()}transitions:${issue.key}`)
    expect(clearAll).not.toHaveBeenCalled()
  })

  it('keeps cached protected work visible only for a transient revalidation failure', async () => {
    seedCurrentWork(user, queuedWorkflowIssue)
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
    fireEvent.click(screen.getByRole('button', { name: 'История и сообщения' }))
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
      trackerIssue: vi.fn(async () => queuedWorkflowIssue),
      taskMessage: vi.fn(async () => {
        throw new ApiError(401, 'session_expired')
      }),
    })
    renderWorkbench({ client, onAuthorizationFailure })

    await screen.findByRole('heading', { name: issue.summary })
    await openTaskChat()
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

it('does not publish a lifecycle message result after the principal changes', async () => {
  const pending = deferred<TaskTimelineItem>()
  const client = apiClient({ trackerIssue: vi.fn(async () => queuedWorkflowIssue), taskMessage: vi.fn(() => pending.promise) })
  const tree = (nextUser = user) => <Harness><IssueWorkbench apiClient={client}
    user={nextUser} selectedPark={park} issueKey={issue.key} state={{ ...state, detailTab: 'chat' }}
    onCloseIssue={vi.fn()} onAuthorizationFailure={vi.fn(async () => undefined)}
    onOpenIssue={vi.fn()} onStateChange={vi.fn()} /></Harness>
  const view = render(tree())
  const composer = await screen.findByRole('textbox', { name: ru.tracker.comments })
  fireEvent.change(composer, { target: { value: 'Работа начата' } })
  fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
  await waitFor(() => expect(client.taskMessage).toHaveBeenCalledOnce())
  vi.mocked(client.trackerIssue).mockImplementation(() => new Promise(() => undefined))
  view.rerender(tree({ ...user, id: 99, username: 'different-principal' }))
  await act(async () => { pending.resolve(taskMessageResult('Работа начата')) })
  expect(screen.queryByText('Действие выполнено')).not.toBeInTheDocument()
  expect(screen.queryByText(issue.summary)).not.toBeInTheDocument()
})

it('refreshes a conflicting workflow immediately and preserves the message draft for review', async () => {
  const client = apiClient({ trackerIssue: vi.fn(async () => queuedWorkflowIssue), taskMessage: vi.fn(async () => { throw new ApiError(409, 'tracker_state_conflict') }) })
  renderWorkbench({ client })
  await screen.findByRole('heading', { name: issue.summary })
  await openTaskChat()
  vi.mocked(client.trackerIssue).mockResolvedValue(reviewWorkflowIssue)
  fireEvent.change(screen.getByRole('textbox', { name: ru.tracker.comments }), { target: { value: 'Не потерять этот черновик' } })
  fireEvent.click(screen.getByRole('button', { name: ru.tracker.commentSubmit }))
  await screen.findByText(/Статус или исполнитель изменились/)
  await screen.findByText('На проверке')
  expect(screen.getByRole('textbox', { name: ru.tracker.comments })).toHaveValue('Не потерять этот черновик')
})

it('claims in one action without fetching or choosing the final repair component', async () => {
  const mechanic: User = { ...user, username: 'mech1', role: 'mechanic' }
  const taskClaim = vi.fn(async () => taskActionResult('claim'))
  const taskRepairOptions = vi.fn(async () => { throw new ApiError(502, 'tracker_upstream_error') })
  renderWorkbench({ currentUser: mechanic, selectedIssue: '', client: apiClient({ taskClaim, taskRepairOptions }) })
  fireEvent.click(await screen.findByRole('button', { name: 'Взять в работу' }))
  await waitFor(() => expect(taskClaim).toHaveBeenCalledWith(issue.key, expect.any(String)))
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
})

it('queues a claim without requiring the repair catalog first', async () => {
  const mechanic: User = { ...user, username: 'mech', role: 'mechanic', tracker_login: 'mech' }
  const taskClaim = vi.fn(async () => taskActionResult('claim'))
  const enqueueAction = vi.fn(async () => undefined)
  const sync: SyncContextValue = {
    state: { status: 'idle', pending: 0, conflicts: 0 }, enqueueAction, enqueueMedia: vi.fn(),
    syncNow: vi.fn(), cancelAction: vi.fn(), resolveConflict: vi.fn(), findAction: vi.fn(async () => undefined), subscribeAction: vi.fn(() => () => undefined),
  }
  const taskRepairOptions = vi.fn(async () => { throw new ApiError(502, 'tracker_upstream_error') })
  renderWorkbench({ client: apiClient({ taskClaim, taskRepairOptions }), selectedIssue: '', currentUser: mechanic, sync })
  fireEvent.click(await screen.findByRole('button', { name: 'Взять в работу' }))
  await waitFor(() => expect(enqueueAction).toHaveBeenCalledWith(expect.objectContaining({ action: 'claim', payload: { park_id: park.id } })))
  expect(taskClaim).not.toHaveBeenCalled()
})
