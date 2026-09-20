import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openOfflineDb, purgeOfflineScope } from './offlineDb'
import { SyncCoordinator } from './syncCoordinator'
import { SyncEngine } from './syncEngine'
import type { OfflineAction, OfflineScope } from './offlineTypes'

const scope: OfflineScope = { account: '1', role: 'mechanic', permissions: 'tracker.read', park: '1', schema: 1 }
const input = (id: string, dependencies: string[] = []): Omit<OfflineAction, 'state' | 'attempts' | 'createdAt' | 'updatedAt'> => ({
  id, deviceId: 'phone', resourceType: 'tracker_issue', resourceId: 'TASK-1', action: 'comment',
  idempotencyKey: `sync-${id}-key`, baseRevision: null, dependencies, payload: { text: id },
})

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))
afterEach(async () => { await purgeOfflineScope(); vi.restoreAllMocks(); vi.useRealTimers() })

function coordinator(db: Awaited<ReturnType<typeof openOfflineDb>>) {
  return new SyncCoordinator({ ownerId: 'tab', leaseStore: db, now: () => Date.now() })
}

describe('SyncEngine', () => {
  it('persists and sends dependencies in causal order', async () => {
    const db = await openOfflineDb(scope)
    const sent: string[][] = []
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch: async batch => {
      sent.push(batch.actions.map(item => item.client_action_id))
      return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} })), deltas: {}, revisions: {}, revoked_scopes: [] }
    } })
    await engine.enqueueAction(input('comment'))
    await engine.enqueueAction(input('review', ['comment']))

    await engine.syncNow('test')

    expect(sent).toEqual([['comment', 'review']])
    expect((await db.getAction('comment'))?.state).toBe('confirmed')
    expect((await db.getAction('review'))?.state).toBe('confirmed')
  })

  it('limits a weak-link batch to two actions', async () => {
    const db = await openOfflineDb(scope)
    const sizes: number[] = []
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', weakLink: () => true, sendBatch: async batch => {
      sizes.push(batch.actions.length)
      return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} })), deltas: {}, revisions: {}, revoked_scopes: [] }
    } })
    await engine.enqueueAction(input('one')); await engine.enqueueAction(input('two')); await engine.enqueueAction(input('three'))

    await engine.syncNow('first')
    await engine.syncNow('second')

    expect(sizes).toEqual([2, 1])
  })

  it('keeps the same action ready after transport failure and schedules a jittered retry', async () => {
    const db = await openOfflineDb(scope)
    let retry: (() => void) | undefined
    const scheduleRetry = vi.fn((callback: () => void) => {
      retry = callback
      return 1 as unknown as ReturnType<typeof setTimeout>
    })
    const sendBatch = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValueOnce({
      results: [{ client_action_id: 'same', state: 'confirmed', code: null, result: {} }], deltas: {}, revisions: {}, revoked_scopes: [],
    })
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch, random: () => 0, scheduleRetry })
    await engine.enqueueAction(input('same'))

    await engine.syncNow('first')
    expect(await db.getAction('same')).toMatchObject({ id: 'same', idempotencyKey: 'sync-same-key', state: 'ready', attempts: 1 })
    expect(scheduleRetry).toHaveBeenCalledWith(expect.any(Function), 1_000)
    retry?.()
    await vi.waitFor(() => expect(sendBatch).toHaveBeenCalledTimes(2))

    expect((await db.getAction('same'))?.state).toBe('confirmed')
    engine.dispose()
  })

  it('retries a temporary server result instead of freezing it in attention', async () => {
    const db = await openOfflineDb(scope)
    const scheduleRetry = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>)
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', random: () => 0, scheduleRetry, sendBatch: async batch => ({
      results: [{ client_action_id: batch.actions[0].client_action_id, state: 'attention', code: 'tracker_upstream_error', result: null }],
      deltas: {}, revisions: {}, revoked_scopes: [],
    }) })
    await engine.enqueueAction(input('temporary'))

    await engine.syncNow('test')

    expect(await db.getAction('temporary')).toMatchObject({ state: 'ready', attempts: 1 })
    expect(scheduleRetry).toHaveBeenCalledWith(expect.any(Function), 1_000)
    engine.dispose()
  })

  it('backs off transient media failures and stops retrying permanent client errors', async () => {
    const db = await openOfflineDb(scope)
    const scheduleRetry = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>)
    const uploadMedia = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockRejectedValueOnce({ status: 400 })
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', random: () => 0, scheduleRetry, uploadMedia, sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }) })
    const media = { id: 'photo', actionId: 'review', issueKey: 'TASK-1', name: 'robot.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 1 }
    await engine.enqueueMedia(media)
    expect(engine.getState().pending).toBe(1)

    await engine.syncNow('offline')
    expect(await db.getMedia('photo')).toMatchObject({ state: 'ready', attempts: 1 })
    expect(scheduleRetry).toHaveBeenLastCalledWith(expect.any(Function), 1_000)

    await engine.syncNow('bad-file')
    expect(await db.getMedia('photo')).toMatchObject({ state: 'attention', attempts: 2 })
    expect(engine.getState()).toMatchObject({ pending: 1, conflicts: 1, status: 'attention' })
    expect(scheduleRetry).toHaveBeenCalledTimes(1)
    engine.dispose()
  })

  it('pauses a conflicting action and forwards revoked scopes', async () => {
    const db = await openOfflineDb(scope)
    const revoked = vi.fn()
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', onRevokedScopes: revoked, sendBatch: async batch => ({
      results: [{ client_action_id: batch.actions[0].client_action_id, state: 'conflict', code: 'task_already_closed', result: null }],
      deltas: {}, revisions: {}, revoked_scopes: ['work:park:1'],
    }) })
    await engine.enqueueAction(input('conflict'))

    await engine.syncNow('test')

    expect(await db.getAction('conflict')).toMatchObject({ state: 'conflict' })
    expect(revoked).toHaveBeenCalledWith(['work:park:1'])
  })

  it('wakes on online and focus and removes every listener on dispose', async () => {
    const db = await openOfflineDb(scope)
    const listeners = new Map<string, EventListener>()
    const environment = {
      addEventListener: vi.fn((name: string, listener: EventListener) => listeners.set(name, listener)),
      removeEventListener: vi.fn((name: string) => listeners.delete(name)),
    }
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', environment, sendBatch: vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] })) })
    const sync = vi.spyOn(engine, 'syncNow').mockResolvedValue(false)
    engine.start()

    listeners.get('online')?.(new Event('online')); listeners.get('focus')?.(new Event('focus'))
    expect(sync).toHaveBeenCalledTimes(3)
    engine.dispose()
    expect(environment.removeEventListener).toHaveBeenCalledTimes(2)
    expect(listeners.size).toBe(0)
  })
})
