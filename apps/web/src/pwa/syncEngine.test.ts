import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openOfflineDb, purgeOfflineScope } from './offlineDb'
import { SyncCoordinator } from './syncCoordinator'
import { NetworkOnlySyncEngine, SyncEngine } from './syncEngine'
import type { OfflineAction, OfflineScope } from './offlineTypes'
import type { SyncBatchRequest } from '../api'
import { activateServiceWorkerWhenSafe } from './registerServiceWorker'

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
  it('keeps a photo queued if its cross-tab lease is lost during upload', async () => {
    const db = await openOfflineDb(scope)
    await db.putMedia({ id: 'lease-photo', actionId: 'lease-review', issueKey: 'TASK-1',
      name: 'robot.jpg', blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a',
      sizeBytes: 5, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    vi.spyOn(db, 'renewLease').mockResolvedValueOnce(true).mockResolvedValue(false)
    let uploadSignal: AbortSignal | undefined
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'tab', leaseStore: db, leaseMs: 30, now: () => 0 }),
      deviceId: 'phone', scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      uploadMedia: async (_media, signal) => {
        uploadSignal = signal
        await new Promise(resolve => setTimeout(resolve, 80))
      },
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }),
    })

    expect(await engine.syncNow('lease-loss')).toBe(false)
    expect(uploadSignal?.aborted).toBe(true)
    expect(await db.getMedia('lease-photo')).toMatchObject({ state: 'uploading' })
    engine.dispose()
  })

  it('rechecks an expired lease before confirming a photo after a suspended tab resumes', async () => {
    const db = await openOfflineDb(scope)
    await db.putMedia({ id: 'sleep-photo', actionId: 'sleep-review', issueKey: 'TASK-1',
      name: 'robot.jpg', blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a',
      sizeBytes: 5, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    let clock = 0
    let uploads = 0
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'tab', leaseStore: db, now: () => clock, leaseMs: 90 }),
      deviceId: 'phone', now: () => clock,
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      uploadMedia: async () => { if (++uploads === 1) clock = 91 },
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }),
    })

    expect(await engine.syncNow('resumed-tab')).toBe(false)
    expect(await db.getMedia('sleep-photo')).toMatchObject({ state: 'uploading' })
    await engine.syncNow('next-leader')
    expect(uploads).toBe(2)
    expect(await db.getMedia('sleep-photo')).toMatchObject({ state: 'confirmed' })
    engine.dispose()
  })

  it('does not confirm a photo when another tab takes the lease after renewal', async () => {
    const db = await openOfflineDb(scope)
    await db.putMedia({ id: 'takeover-photo', actionId: 'takeover-review', issueKey: 'TASK-1',
      name: 'robot.jpg', blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a',
      sizeBytes: 5, state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    let clock = 0
    let renewals = 0
    const renewLease = db.renewLease.bind(db)
    vi.spyOn(db, 'renewLease').mockImplementation(async (owner, now, leaseMs) => {
      const renewed = await renewLease(owner, now, leaseMs)
      if (++renewals === 2) {
        clock = now + leaseMs
        expect(await db.claimLease('tab-two', clock, leaseMs)).toBe(true)
      }
      return renewed
    })
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'tab-one', leaseStore: db, now: () => clock, leaseMs: 90_000 }),
      deviceId: 'phone', now: () => clock,
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      uploadMedia: async () => {},
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }),
    })

    expect(await engine.syncNow('takeover')).toBe(false)
    expect(await db.getMedia('takeover-photo')).toMatchObject({ state: 'uploading' })
    expect(await db.renewLease('tab-two', clock, 90_000)).toBe(true)
    engine.dispose()
  })

  it('does not confirm a batch when another tab takes the lease after renewal', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('takeover-action'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    let clock = 0
    let renewals = 0
    const renewLease = db.renewLease.bind(db)
    vi.spyOn(db, 'renewLease').mockImplementation(async (owner, now, leaseMs) => {
      const renewed = await renewLease(owner, now, leaseMs)
      if (++renewals === 2) {
        clock = now + leaseMs
        expect(await db.claimLease('tab-two', clock, leaseMs)).toBe(true)
      }
      return renewed
    })
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'tab-one', leaseStore: db, now: () => clock, leaseMs: 90_000 }),
      deviceId: 'phone', now: () => clock,
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      sendBatch: async () => ({ results: [{ client_action_id: 'takeover-action', state: 'confirmed', code: null, result: {} }], deltas: {}, revisions: {}, revoked_scopes: [] }),
    })

    expect(await engine.syncNow('takeover')).toBe(false)
    expect(await db.getAction('takeover-action')).toMatchObject({ state: 'sending' })
    expect(await db.renewLease('tab-two', clock, 90_000)).toBe(true)
    engine.dispose()
  })

  it('retries a server-accepted batch with the same identity after its lease expires', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('sleep-action'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    let clock = 0
    const identities: string[] = []
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'tab', leaseStore: db, now: () => clock, leaseMs: 90 }),
      deviceId: 'phone', now: () => clock,
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      sendBatch: async batch => {
        identities.push(batch.actions[0].idempotency_key)
        if (identities.length === 1) clock = 91
        return { results: [{ client_action_id: 'sleep-action', state: 'confirmed', code: null, result: {} }], deltas: {}, revisions: {}, revoked_scopes: [] }
      },
    })

    expect(await engine.syncNow('resumed-tab')).toBe(false)
    expect(await db.getAction('sleep-action')).toMatchObject({ state: 'sending' })
    expect(await engine.syncNow('next-leader')).toBe(true)
    expect(identities).toEqual(['sync-sleep-action-key', 'sync-sleep-action-key'])
    expect(await db.getAction('sleep-action')).toMatchObject({ state: 'confirmed' })
    engine.dispose()
  })

  it('keeps a failed batch pending if the lease is taken before retry bookkeeping', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('retry-takeover'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    let clock = 0
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'tab-one', leaseStore: db, now: () => clock, leaseMs: 90 }),
      deviceId: 'phone', now: () => clock,
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      sendBatch: async () => {
        clock = 91
        expect(await db.claimLease('tab-two', clock, 90)).toBe(true)
        throw new Error('network')
      },
    })

    expect(await engine.syncNow('takeover-error')).toBe(false)
    expect(await db.getAction('retry-takeover')).toMatchObject({ state: 'sending' })
    engine.dispose()
  })

  it('aborts a network-only photo upload when its account scope is disposed', async () => {
    let release!: () => void
    const uploading = new Promise<void>(resolve => { release = resolve })
    let receivedSignal: AbortSignal | undefined
    const uploadMedia = vi.fn(async (_media, signal?: AbortSignal) => {
      receivedSignal = signal
      await uploading
      if (signal?.aborted) throw new Error('session_changed')
    })
    const engine = new NetworkOnlySyncEngine({ deviceId: 'account-1', uploadMedia,
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }) })
    const pending = engine.enqueueMedia({ id: 'media-dispose', actionId: 'review-dispose', issueKey: 'TASK-1',
      name: 'robot.jpg', blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'hash', sizeBytes: 5 })
    await vi.waitFor(() => expect(uploadMedia).toHaveBeenCalledOnce())
    engine.dispose()
    try { expect(receivedSignal?.aborted).toBe(true) } finally { release() }
    await expect(pending).rejects.toThrow('session_changed')
  })

  it('retains a confirmed schedule result for a restarted view', async () => {
    const db = await openOfflineDb(scope)
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone',
      sendBatch: async batch => ({
        results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: { entry: { id: 'shift-1', kind: 'shift' } } })),
        deltas: {}, revisions: {}, revoked_scopes: [],
      }),
    })
    await engine.enqueueAction({ ...input('shift'), resourceType: 'schedule_entry', resourceId: 'shift', action: 'schedule_create', payload: { park_id: 1 } })
    await engine.syncNow('test')

    expect(await engine.listActions()).toEqual([expect.objectContaining({
      id: 'shift', state: 'confirmed', result: { entry: { id: 'shift-1', kind: 'shift' } },
    })])
    engine.dispose()
  })
  it('atomically stores linked review media and action before starting upload', async () => {
    const db = await openOfflineDb(scope)
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone',
      uploadMedia: vi.fn(async () => {}),
      sendBatch: vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] })),
    })
    const review = {
      ...input('review-linked'), action: 'submit_review',
      payload: { media_id: 'media-linked' }, dependencies: ['media-linked'],
    }

    await engine.enqueueMedia({
      id: 'media-linked', actionId: review.id, issueKey: 'TASK-1', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'same-sha', sizeBytes: 5,
    }, review)

    expect(await db.getMedia('media-linked')).toMatchObject({ actionId: review.id, state: 'ready' })
    expect(await db.getAction(review.id)).toMatchObject({ id: review.id, state: 'ready', idempotencyKey: review.idempotencyKey })
    engine.dispose()
  })

  it.each(['media_upload_missing', 'media_dependency_pending'] as const)(
    'reuploads retained media after %s and retries the same review action identity', async recoveryCode => {
    const db = await openOfflineDb(scope)
    await db.putMedia({
      id: 'media-legacy', actionId: 'review-legacy', issueKey: 'TASK-1', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'same-sha', sizeBytes: 5,
      state: 'confirmed', attempts: 0, createdAt: 10, updatedAt: 20,
    })
    await db.putAction({
      ...input('review-legacy'), action: 'submit_review', idempotencyKey: 'stable-review-key',
      payload: { media_id: 'media-legacy' }, state: 'ready', attempts: 0, createdAt: 10, updatedAt: 20,
    })
    const uploadMedia = vi.fn(async () => {})
    const sent: SyncBatchRequest['actions'] = []
    let attempt = 0
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone', uploadMedia,
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>), random: () => 0,
      sendBatch: async batch => {
        sent.push(...batch.actions)
        attempt += 1
        return {
          results: batch.actions.map(item => attempt === 1
            ? { client_action_id: item.client_action_id, state: 'conflict' as const, code: recoveryCode, result: null }
            : { client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} }),
          deltas: {}, revisions: {}, revoked_scopes: [],
        }
      },
    })

    await engine.syncNow('legacy-missing')
    expect(await db.getMedia('media-legacy')).toMatchObject({ state: 'ready' })
    expect(await db.getAction('review-legacy')).toMatchObject({ state: 'ready' })

    await engine.syncNow('legacy-reupload')
    expect(uploadMedia).toHaveBeenCalledOnce()
    expect(sent).toHaveLength(2)
    expect(sent[0]).toMatchObject({ client_action_id: 'review-legacy', idempotency_key: 'stable-review-key' })
    expect(sent[1]).toMatchObject({ client_action_id: 'review-legacy', idempotency_key: 'stable-review-key' })
    expect(await db.getMedia('media-legacy')).toMatchObject({ state: 'confirmed' })
    expect(await db.getAction('review-legacy')).toMatchObject({ state: 'confirmed' })
    engine.dispose()
    },
  )

  it('recovers interrupted action and media transfers on restart with the same identities', async () => {
    const db = await openOfflineDb(scope)
    await db.putMedia({
      id: 'media-restart', actionId: 'review-restart', issueKey: 'TASK-1', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'same-sha', sizeBytes: 5,
      state: 'uploading', attempts: 0, createdAt: 10, updatedAt: 20,
    })
    await db.putAction({
      ...input('review-restart'), action: 'submit_review', idempotencyKey: 'stable-review-key',
      payload: { media_id: 'media-restart' }, state: 'sending', attempts: 0, createdAt: 10, updatedAt: 20,
    })
    const uploaded: string[] = []
    const sent: SyncBatchRequest['actions'] = []
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone',
      uploadMedia: async media => { uploaded.push(media.id) },
      sendBatch: async batch => {
        sent.push(...batch.actions)
        return {
          results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} })),
          deltas: {}, revisions: {}, revoked_scopes: [],
        }
      },
    })

    await engine.syncNow('restart')

    expect(uploaded).toEqual(['media-restart'])
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({
      client_action_id: 'review-restart', idempotency_key: 'stable-review-key',
      payload: { media_id: 'media-restart' },
    })
    expect(await db.getMedia('media-restart')).toMatchObject({ state: 'confirmed', attempts: 1 })
    expect(await db.getAction('review-restart')).toMatchObject({ state: 'confirmed', attempts: 1 })
    expect(engine.getState()).toMatchObject({ status: 'idle', pending: 0 })
    engine.dispose()
  })

  it('waits for server acknowledgement when IndexedDB is unavailable', async () => {
    vi.stubGlobal('indexedDB', undefined)
    const calls: string[][] = []
    const network = new NetworkOnlySyncEngine({ deviceId: 'phone', sendBatch: async batch => {
      calls.push(batch.actions.map(item => item.client_action_id))
      return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })), deltas: {}, revisions: {}, revoked_scopes: [] }
    } })
    const first = network.enqueueOptimistic(input('one'), { text: 'one' })
    const second = network.enqueueOptimistic(input('two'), { text: 'two' })
    expect(network.getProjection('tracker_issue', 'TASK-1')).toEqual({ text: 'two' })
    expect(calls).toEqual([])

    await Promise.all([first, second])

    expect(calls).toEqual([['one', 'two']])
    expect(network.getProjection('tracker_issue', 'TASK-1')).toBeUndefined()
    network.dispose()
  })

  it('rolls back a network-only projection when the send fails', async () => {
    const network = new NetworkOnlySyncEngine({ deviceId: 'phone', sendBatch: async () => { throw new TypeError('offline') } })
    const pending = network.enqueueOptimistic(input('one'), { text: 'draft' })
    await expect(pending).rejects.toThrow('offline')
    expect(network.getProjection('tracker_issue', 'TASK-1')).toBeUndefined()
    expect(network.getState().status).toBe('attention')
    network.dispose()
  })

  it('reports direct-send action states to subscribers', async () => {
    const network = new NetworkOnlySyncEngine({ deviceId: 'phone', sendBatch: async batch => ({
      results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })), deltas: {}, revisions: {}, revoked_scopes: [],
    }) })
    const observed: string[] = []
    network.subscribeAction('one', action => { if (action) observed.push(action.state) })

    await network.enqueueAction(input('one'))

    expect(observed).toContain('ready')
    expect(observed.at(-1)).toBe('confirmed')
    network.dispose()
  })
  it('projects immediately, sends queued writes together, and rolls back a conflict', async () => {
    const db = await openOfflineDb(scope)
    const sent: string[][] = []
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch: async batch => {
      sent.push(batch.actions.map(item => item.client_action_id))
      return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'conflict' as const, code: 'stale', result: null })), deltas: {}, revisions: {}, revoked_scopes: [] }
    } })
    const first = engine.enqueueOptimistic(input('first'), { text: 'draft 1' })
    expect(engine.getProjection('tracker_issue', 'TASK-1')).toEqual({ text: 'draft 1' })
    await first
    await engine.enqueueOptimistic(input('second'), { text: 'draft 2' })
    expect(engine.getProjection('tracker_issue', 'TASK-1')).toEqual({ text: 'draft 2' })
    expect(sent).toEqual([])
    await engine.syncNow('test-batch')
    expect(sent).toEqual([['first', 'second']])
    await vi.waitFor(() => expect(engine.getProjection('tracker_issue', 'TASK-1')).toBeUndefined())
    engine.dispose()
  })

  it('sends directly when durable storage rejects a new action', async () => {
    const db = await openOfflineDb(scope)
    vi.spyOn(db, 'putQueuedAction').mockRejectedValueOnce(new DOMException('full', 'QuotaExceededError'))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch: async batch => ({
      results: [{ client_action_id: batch.actions[0].client_action_id, state: 'confirmed', code: null, result: null }], deltas: {}, revisions: {}, revoked_scopes: [],
    }) })

    await engine.enqueueAction(input('direct'))

    expect(await db.getAction('direct')).toBeUndefined()
    expect(engine.getState()).toMatchObject({ status: 'idle', pending: 0 })
  })
  it('reports revoked park access from a quota fallback batch', async () => {
    const db = await openOfflineDb(scope)
    vi.spyOn(db, 'putQueuedAction').mockRejectedValueOnce(new DOMException('full', 'QuotaExceededError'))
    const onRevokedScopes = vi.fn()
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', onRevokedScopes, sendBatch: async batch => ({
      results: [{ client_action_id: batch.actions[0].client_action_id, state: 'rejected', code: 'park_forbidden', result: null }],
      deltas: {}, revisions: {}, revoked_scopes: ['work:park:1'],
    }) })

    await expect(engine.enqueueAction(input('direct-revoked'))).rejects.toThrow('sync_not_confirmed')
    expect(onRevokedScopes).toHaveBeenCalledWith(['work:park:1'])
    engine.dispose()
  })
  it('batches two quota-denied writes after 150 ms', async () => {
    const db = await openOfflineDb(scope)
    vi.spyOn(db, 'putQueuedAction').mockRejectedValue(new DOMException('full', 'QuotaExceededError'))
    const calls: string[][] = []
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch: async batch => {
      calls.push(batch.actions.map(item => item.client_action_id))
      return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })), deltas: {}, revisions: {}, revoked_scopes: [] }
    } })
    const first = engine.enqueueAction(input('one'))
    const second = engine.enqueueAction(input('two'))
    await new Promise(resolve => setTimeout(resolve, 100))
    expect(calls).toEqual([])
    await Promise.all([first, second])
    expect(calls).toEqual([['one', 'two']])
    engine.dispose()
  })

  it('keeps durable actions, media, and conflicts in state after a quota fallback confirms', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('durable'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    await db.putAction({ ...input('conflict'), state: 'conflict', attempts: 1, createdAt: 1, updatedAt: 1 })
    await db.putMedia({ id: 'photo', actionId: 'durable', issueKey: 'TASK-1', name: 'private.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg', sha256: 'x', sizeBytes: 1, state: 'local', createdAt: 1, updatedAt: 1 })
    vi.spyOn(db, 'putQueuedAction').mockRejectedValueOnce(new DOMException('full', 'QuotaExceededError'))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch: async batch => ({
      results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })), deltas: {}, revisions: {}, revoked_scopes: [],
    }) })
    await engine.enqueueAction(input('network'))
    expect(engine.getState()).toMatchObject({ status: 'attention', pending: 3, conflicts: 1 })
    const postMessage = vi.fn()
    expect(activateServiceWorkerWhenSafe({ waiting: { postMessage } }, engine.getState())).toBe(false)
    expect(postMessage).not.toHaveBeenCalled()
    engine.dispose()
  })

  it('keeps a successful partial network-only batch in waiting state', async () => {
    const network = new NetworkOnlySyncEngine({ deviceId: 'phone', sendBatch: async batch => ({
      results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })), deltas: {}, revisions: {}, revoked_scopes: [],
    }) })
    const promises = Array.from({ length: 21 }, (_, index) => network.enqueueAction(input(`item-${index}`)))
    await network.syncNow('test')
    expect(network.getState()).toMatchObject({ status: 'idle', pending: 1 })
    await Promise.all(promises)
    network.dispose()
  })
  it('shares one receipt for duplicate network-only action ids and rejects changed payloads', async () => {
    const sent: string[][] = []
    const network = new NetworkOnlySyncEngine({ deviceId: 'phone', sendBatch: async batch => {
      sent.push(batch.actions.map(item => item.client_action_id))
      return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })), deltas: {}, revisions: {}, revoked_scopes: [] }
    } })
    const first = network.enqueueAction(input('same-action'))
    const repeat = network.enqueueAction(input('same-action'))

    expect(repeat).toBe(first)
    await expect(network.enqueueAction({ ...input('same-action'), payload: { text: 'changed' } })).rejects.toThrow('sync_payload_conflict')
    await network.syncNow('test')
    await expect(first).resolves.toMatchObject({ state: 'confirmed' })
    expect(sent).toEqual([['same-action']])
    network.dispose()
  })
  it('keeps a durable confirmed receipt on replay without opening the network fallback', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('completed'), state: 'confirmed', attempts: 0, createdAt: 1, updatedAt: 2, result: { message_id: 42 } })
    const sendBatch = vi.fn()
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch })

    await expect(engine.enqueueOptimistic(input('completed'), { text: 'optimistic' }))
      .resolves.toMatchObject({ state: 'confirmed', result: { message_id: 42 } })
    expect(engine.getProjection('tracker_issue', 'TASK-1')).toBeUndefined()
    await expect(engine.enqueueAction({ ...input('completed'), payload: { text: 'changed' } }))
      .rejects.toThrow('sync_payload_conflict')
    expect(await db.getAction('completed')).toMatchObject({ state: 'confirmed', result: { message_id: 42 } })
    expect(sendBatch).not.toHaveBeenCalled()
    engine.dispose()
  })
  it('reports a previously confirmed photo as confirmed when its review is repeated', async () => {
    const db = await openOfflineDb(scope)
    const review = { ...input('photo-review'), action: 'submit_review' }
    await db.putAction({ ...review, state: 'confirmed', attempts: 0, createdAt: 1, updatedAt: 2 })
    const photo = {
      id: 'review-photo', actionId: review.id, issueKey: 'TASK-1', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'photo-digest', sizeBytes: 5,
    }
    await db.putMedia({ ...photo, state: 'confirmed', attempts: 0, createdAt: 1, updatedAt: 2 })
    const sendBatch = vi.fn()
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch })

    const result = await engine.enqueueMedia(photo, review)

    expect(result.state).toBe('confirmed')
    expect(await db.getMedia(photo.id)).toMatchObject({ state: 'confirmed' })
    expect(sendBatch).not.toHaveBeenCalled()
    engine.dispose()
  })
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

  it('keeps enqueue order when two task actions share the same device clock tick', async () => {
    const db = await openOfflineDb(scope)
    const sent: string[][] = []
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone', now: () => 1_000,
      sendBatch: async batch => {
        sent.push(batch.actions.map(item => item.client_action_id))
        return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} })), deltas: {}, revisions: {}, revoked_scopes: [] }
      },
    })
    await engine.enqueueAction(input('z-comment'))
    await engine.enqueueAction({ ...input('a-handoff'), action: 'handoff' })
    await engine.syncNow('same-clock-tick')
    expect(sent).toEqual([['z-comment', 'a-handoff']])
    engine.dispose()
  })

  it('keeps task action order after a PWA reload with an unchanged device clock', async () => {
    const firstDb = await openOfflineDb(scope)
    const first = new SyncEngine({
      db: firstDb, coordinator: coordinator(firstDb), deviceId: 'phone', now: () => 1_000,
      sendBatch: vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] })),
    })
    await first.enqueueAction(input('z-comment'))
    first.dispose()

    const secondDb = await openOfflineDb(scope)
    const sent: string[][] = []
    const second = new SyncEngine({
      db: secondDb, coordinator: coordinator(secondDb), deviceId: 'phone', now: () => 1_000,
      sendBatch: async batch => {
        sent.push(batch.actions.map(item => item.client_action_id))
        return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} })), deltas: {}, revisions: {}, revoked_scopes: [] }
      },
    })
    await second.enqueueAction({ ...input('a-handoff'), action: 'handoff' })
    await second.syncNow('after-reload')
    expect(sent).toEqual([['z-comment', 'a-handoff']])
    second.dispose()
  })

  it('shares action order across two tabs with the same device clock', async () => {
    const firstDb = await openOfflineDb(scope)
    const secondDb = await openOfflineDb(scope)
    const sent: string[][] = []
    const first = new SyncEngine({
      db: firstDb, coordinator: coordinator(firstDb), deviceId: 'phone', now: () => 1_000,
      sendBatch: vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] })),
    })
    const second = new SyncEngine({
      db: secondDb, coordinator: coordinator(secondDb), deviceId: 'phone', now: () => 1_000,
      sendBatch: async batch => {
        sent.push(batch.actions.map(item => item.client_action_id))
        return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} })), deltas: {}, revisions: {}, revoked_scopes: [] }
      },
    })
    await first.enqueueAction(input('z-comment'))
    await second.enqueueAction({ ...input('a-handoff'), action: 'handoff' })
    await second.syncNow('other-tab')
    expect(sent).toEqual([['z-comment', 'a-handoff']])
    first.dispose()
    second.dispose()
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

  it('keeps two robot actions separate through one offline retry batch', async () => {
    const db = await openOfflineDb(scope)
    const sent: Array<Array<{ id: string; issue: string; key: string }>> = []
    let available = false
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone',
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      sendBatch: async batch => {
        sent.push(batch.actions.map(item => ({ id: item.client_action_id, issue: item.resource_id, key: item.idempotency_key })))
        if (!available) throw new TypeError('offline')
        return { results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: {} })), deltas: {}, revisions: {}, revoked_scopes: [] }
      },
    })
    await engine.enqueueOptimistic({ ...input('claim-447'), resourceId: 'TASK-447', action: 'claim' }, { state: 'pending' })
    await engine.enqueueOptimistic({ ...input('claim-448'), resourceId: 'TASK-448', action: 'claim' }, { state: 'pending' })
    expect(engine.getProjection('tracker_issue', 'TASK-447')).toEqual({ state: 'pending' })
    expect(engine.getProjection('tracker_issue', 'TASK-448')).toEqual({ state: 'pending' })

    await engine.syncNow('offline')
    expect((await db.getAction('claim-447'))?.state).toBe('ready')
    expect((await db.getAction('claim-448'))?.state).toBe('ready')
    available = true
    await engine.syncNow('reconnected')

    expect(sent).toEqual([
      [{ id: 'claim-447', issue: 'TASK-447', key: 'sync-claim-447-key' }, { id: 'claim-448', issue: 'TASK-448', key: 'sync-claim-448-key' }],
      [{ id: 'claim-447', issue: 'TASK-447', key: 'sync-claim-447-key' }, { id: 'claim-448', issue: 'TASK-448', key: 'sync-claim-448-key' }],
    ])
    expect((await db.getAction('claim-447'))?.state).toBe('confirmed')
    expect((await db.getAction('claim-448'))?.state).toBe('confirmed')
    expect(engine.getProjection('tracker_issue', 'TASK-447')).toBeUndefined()
    expect(engine.getProjection('tracker_issue', 'TASK-448')).toBeUndefined()
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

  it('keeps only one retry timer after repeated failed syncs and cancels it on dispose', async () => {
    const db = await openOfflineDb(scope)
    const callbacks = new Map<number, () => void>()
    const cancelRetry = vi.fn((timer: ReturnType<typeof setTimeout>) => {
      callbacks.delete(timer as unknown as number)
    })
    const scheduleRetry = vi.fn((callback: () => void) => {
      const id = scheduleRetry.mock.calls.length
      callbacks.set(id, callback)
      return id as unknown as ReturnType<typeof setTimeout>
    })
    const sendBatch = vi.fn(async () => { throw new Error('network_offline') })
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone',
      random: () => 0, scheduleRetry, cancelRetry, sendBatch })
    await engine.enqueueAction(input('retry-once'))

    await engine.syncNow('first')
    const staleCallback = callbacks.get(1)
    await engine.syncNow('manual')

    expect(cancelRetry).toHaveBeenCalledWith(1)
    expect([...callbacks.keys()]).toEqual([2])
    engine.dispose()
    expect(cancelRetry).toHaveBeenCalledWith(2)
    staleCallback?.()
    expect(callbacks.size).toBe(0)
    expect(sendBatch).toHaveBeenCalledTimes(2)
  })

  it.each(['dependency_missing', 'dependency_failed'])(
    'keeps a server %s result visible instead of retrying forever', async code => {
      const db = await openOfflineDb(scope)
      await db.putAction({ ...input('saved-comment'), state: 'confirmed', attempts: 0, createdAt: 1, updatedAt: 1 })
      const scheduleRetry = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>)
      const sendBatch = vi.fn(async batch => ({
        results: [{ client_action_id: batch.actions[0].client_action_id, state: 'attention' as const, code, result: null }],
        deltas: {}, revisions: {}, revoked_scopes: [],
      }))
      const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone',
        scheduleRetry, sendBatch })
      await engine.enqueueAction({ ...input('review', ['saved-comment']), action: 'submit_review' })

      await engine.syncNow('test')

      expect(await db.getAction('review')).toMatchObject({ state: 'attention', result: { code } })
      expect(engine.getState()).toMatchObject({ status: 'attention', pending: 1, conflicts: 1 })
      expect(scheduleRetry).not.toHaveBeenCalled()
      await engine.syncNow('manual')
      expect(sendBatch).toHaveBeenCalledTimes(1)
      engine.dispose()
    },
  )

  it('retries a review after its comment had a temporary server failure', async () => {
    const db = await openOfflineDb(scope)
    let retry: (() => void) | undefined
    const scheduleRetry = vi.fn((callback: () => void) => {
      retry = callback
      return 1 as unknown as ReturnType<typeof setTimeout>
    })
    let attempt = 0
    const sendBatch = vi.fn(async (batch: SyncBatchRequest) => {
      attempt += 1
      return {
        results: batch.actions.map(item => ({
          client_action_id: item.client_action_id,
          state: attempt === 1 ? 'attention' as const : 'confirmed' as const,
          code: attempt === 1 ? item.action === 'comment' ? 'tracker_upstream_error' : 'dependency_failed' : null,
          result: attempt === 1 ? null : {},
        })),
        deltas: {}, revisions: {}, revoked_scopes: [],
      }
    })
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone',
      random: () => 0, scheduleRetry, sendBatch })
    await engine.enqueueAction(input('comment-before-review'))
    await engine.enqueueAction({ ...input('review-after-comment', ['comment-before-review']), action: 'submit_review' })

    await engine.syncNow('first')

    expect(await db.getAction('comment-before-review')).toMatchObject({ state: 'ready', attempts: 1 })
    expect(await db.getAction('review-after-comment')).toMatchObject({ state: 'ready', attempts: 1 })
    expect(scheduleRetry).toHaveBeenCalledWith(expect.any(Function), 1_000)
    retry?.()
    await vi.waitFor(() => expect(sendBatch).toHaveBeenCalledTimes(2))
    await vi.waitFor(async () => expect((await db.getAction('review-after-comment'))?.state).toBe('confirmed'))
    engine.dispose()
  })

  it('retains a final rejection reason for recovery instead of silently retrying', async () => {
    const db = await openOfflineDb(scope)
    const scheduleRetry = vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>)
    const sendBatch = vi.fn(async (batch: SyncBatchRequest) => ({
      results: [{ client_action_id: batch.actions[0].client_action_id, state: 'rejected' as const,
        code: 'park_forbidden', result: null }],
      deltas: {}, revisions: {}, revoked_scopes: [],
    }))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone',
      scheduleRetry, sendBatch })
    await engine.enqueueAction(input('revoked-park'))

    await engine.syncNow('test')

    expect(await db.getAction('revoked-park')).toMatchObject({ state: 'attention', result: { code: 'park_forbidden' } })
    expect(scheduleRetry).not.toHaveBeenCalled()
    await engine.syncNow('manual')
    expect(sendBatch).toHaveBeenCalledTimes(1)
    engine.dispose()
  })

  it('retries only the unacknowledged action when a batch response omits its result', async () => {
    const db = await openOfflineDb(scope)
    let retry: (() => void) | undefined
    const scheduleRetry = vi.fn((callback: () => void) => {
      retry = callback
      return 1 as unknown as ReturnType<typeof setTimeout>
    })
    const sent: string[][] = []
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone', random: () => 0, scheduleRetry,
      sendBatch: async batch => {
        sent.push(batch.actions.map(action => action.client_action_id))
        return {
          results: batch.actions.filter(action => sent.length > 1 || action.client_action_id === 'first')
            .map(action => ({ client_action_id: action.client_action_id, state: 'confirmed' as const, code: null, result: {} })),
          deltas: {}, revisions: {}, revoked_scopes: [],
        }
      },
    })
    await engine.enqueueAction(input('first'))
    await engine.enqueueAction(input('second'))

    await engine.syncNow('first-attempt')

    expect(await db.getAction('first')).toMatchObject({ state: 'confirmed' })
    expect(await db.getAction('second')).toMatchObject({ state: 'ready', attempts: 1, idempotencyKey: 'sync-second-key' })
    expect(scheduleRetry).toHaveBeenCalledWith(expect.any(Function), 1_000)
    retry?.()
    await vi.waitFor(() => expect(sent).toEqual([['first', 'second'], ['second']]))
    await vi.waitFor(async () => expect((await db.getAction('second'))?.state).toBe('confirmed'))
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

  it('holds a review behind a permanently rejected photo until a manual retry succeeds', async () => {
    const db = await openOfflineDb(scope)
    const uploadMedia = vi.fn().mockRejectedValueOnce({ status: 400 }).mockResolvedValue(undefined)
    const sendBatch = vi.fn(async (batch: SyncBatchRequest) => ({
      results: batch.actions.map(action => ({ client_action_id: action.client_action_id, state: 'confirmed' as const, code: null, result: {} })),
      deltas: {}, revisions: {}, revoked_scopes: [],
    }))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', uploadMedia, sendBatch })
    const review = { ...input('review-photo'), action: 'submit_review', payload: { media_id: 'photo-review' }, dependencies: ['photo-review'] }
    await engine.enqueueMedia({
      id: 'photo-review', actionId: review.id, issueKey: 'TASK-1', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 5,
    }, review)

    await engine.syncNow('first')
    expect(await db.getMedia('photo-review')).toMatchObject({ state: 'attention', attempts: 1 })
    await engine.syncNow('event')
    expect(uploadMedia).toHaveBeenCalledTimes(1)
    expect(sendBatch).not.toHaveBeenCalled()
    expect(await db.getAction('review-photo')).toMatchObject({ state: 'ready' })

    await engine.syncNow('manual')
    expect(uploadMedia).toHaveBeenCalledTimes(2)
    expect(sendBatch).toHaveBeenCalledOnce()
    expect(await db.getMedia('photo-review')).toMatchObject({ state: 'confirmed' })
    expect(await db.getAction('review-photo')).toMatchObject({ state: 'confirmed' })
    engine.dispose()
  })

  it('does not claim cancellation of an action already being sent', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('in-flight'), state: 'sending', attempts: 0, createdAt: 1, updatedAt: 1 })
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone',
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }) })

    await expect(engine.cancelAction('in-flight')).rejects.toThrow('sync_busy')
    expect(await db.getAction('in-flight')).toMatchObject({ state: 'sending' })
    engine.dispose()
  })

  it('does not cancel an action after another tab takes the lease', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('cancel-takeover'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    let clock = 0
    const renewLease = db.renewLease.bind(db)
    vi.spyOn(db, 'renewLease').mockImplementation(async (owner, now, leaseMs) => {
      const renewed = await renewLease(owner, now, leaseMs)
      clock = now + leaseMs
      expect(await db.claimLease('tab-two', clock, leaseMs)).toBe(true)
      return renewed
    })
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'tab-one', leaseStore: db, now: () => clock, leaseMs: 90_000 }),
      deviceId: 'phone', now: () => clock,
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }),
    })

    await expect(engine.cancelAction('cancel-takeover')).rejects.toThrow('sync_busy')
    expect(await db.getAction('cancel-takeover')).toMatchObject({ state: 'ready' })
    engine.dispose()
  })

  it('atomically cancels an unsent review and its retained photo', async () => {
    const db = await openOfflineDb(scope)
    const uploadMedia = vi.fn(async () => {})
    const sendBatch = vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', uploadMedia, sendBatch })
    const review = { ...input('review-cancel'), action: 'submit_review', payload: { media_id: 'photo-cancel' }, dependencies: ['photo-cancel'] }
    await engine.enqueueMedia({
      id: 'photo-cancel', actionId: review.id, issueKey: 'TASK-1', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 5,
    }, review)

    await engine.cancelAction(review.id)
    expect(await db.getAction(review.id)).toMatchObject({ state: 'cancelled' })
    expect(await db.getMedia('photo-cancel')).toBeUndefined()
    expect(engine.getState()).toMatchObject({ pending: 0, conflicts: 0 })
    await engine.syncNow('event')
    expect(uploadMedia).not.toHaveBeenCalled()
    expect(sendBatch).not.toHaveBeenCalled()
    engine.dispose()
  })

  it('keeps a pending review and its comment dependency together', async () => {
    const db = await openOfflineDb(scope)
    const sendBatch = vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch })
    await db.putAction({ ...input('repair-comment'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    await db.putAction({ ...input('review', ['repair-comment', 'review-photo']), action: 'submit_review',
      state: 'ready', attempts: 0, createdAt: 2, updatedAt: 2 })
    await db.putMedia({ id: 'review-photo', actionId: 'review', issueKey: 'TASK-1', name: 'robot.jpg',
      blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 5,
      state: 'confirmed', attempts: 0, createdAt: 1, updatedAt: 1 })

    await expect(engine.cancelAction('repair-comment')).rejects.toThrow('sync_dependency_in_use')
    expect(await db.getAction('repair-comment')).toMatchObject({ state: 'ready' })
    expect(await db.getAction('review')).toMatchObject({ state: 'ready' })
    expect(sendBatch).not.toHaveBeenCalled()
    engine.dispose()
  })

  it('never sends a review whose persisted comment dependency was cancelled', async () => {
    const db = await openOfflineDb(scope)
    const sendBatch = vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch })
    await db.putAction({ ...input('cancelled-comment'), state: 'cancelled', attempts: 0, createdAt: 1, updatedAt: 1 })
    await db.putAction({ ...input('review', ['cancelled-comment']), action: 'submit_review',
      state: 'ready', attempts: 0, createdAt: 2, updatedAt: 2 })

    expect(await engine.syncNow('manual')).toBe(false)
    expect(sendBatch).not.toHaveBeenCalled()
    expect(await db.getAction('review')).toMatchObject({ state: 'attention', result: { code: 'offline_dependency_missing' } })
    expect(engine.getState()).toMatchObject({ status: 'attention', pending: 1, conflicts: 1 })
    engine.dispose()
  })

  it('holds a review when its comment dependency is missing locally', async () => {
    const db = await openOfflineDb(scope)
    const sendBatch = vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch })
    await db.putAction({ ...input('review', ['missing-comment']), action: 'submit_review',
      state: 'ready', attempts: 0, createdAt: 2, updatedAt: 2 })

    expect(await engine.syncNow('manual')).toBe(false)
    expect(sendBatch).not.toHaveBeenCalled()
    expect(await db.getAction('review')).toMatchObject({ state: 'attention', result: { code: 'offline_dependency_missing' } })
    expect(engine.getState()).toMatchObject({ status: 'attention', pending: 1, conflicts: 1 })
    engine.dispose()
  })

  it('reports cancellation as complete if the lease expires after the durable commit', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('cancel-committed'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    const renewLease = db.renewLease.bind(db)
    let renewals = 0
    vi.spyOn(db, 'renewLease').mockImplementation(async (owner, now, leaseMs) => {
      if (++renewals === 2) return false
      return renewLease(owner, now, leaseMs)
    })
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone',
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }) })

    await expect(engine.cancelAction('cancel-committed')).resolves.toBeUndefined()
    expect(await db.getAction('cancel-committed')).toMatchObject({ state: 'cancelled' })
    engine.dispose()
  })

  it('does not overwrite a newer tab revision after losing the lease', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('revision-race'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    let clock = 0
    let writes = 0
    const transactionIfLease = db.transactionIfLease.bind(db)
    vi.spyOn(db, 'transactionIfLease').mockImplementation(async (owner, now, mutator) => {
      const committed = await transactionIfLease(owner, now, mutator)
      if (++writes === 2) {
        clock = 91
        expect(await db.claimLease('new-tab', clock, 90)).toBe(true)
        await db.setRevision('tasks', '2')
      }
      return committed
    })
    const engine = new SyncEngine({ db, coordinator: new SyncCoordinator({ ownerId: 'old-tab', leaseStore: db, now: () => clock, leaseMs: 90 }),
      deviceId: 'phone', now: () => clock,
      scheduleRetry: vi.fn(() => 1 as unknown as ReturnType<typeof setTimeout>),
      sendBatch: async () => ({ results: [{ client_action_id: 'revision-race', state: 'confirmed', code: null, result: {} }],
        deltas: {}, revisions: { tasks: 1 }, revoked_scopes: [] }) })

    await engine.syncNow('revision-race')
    expect(await db.getRevision('tasks')).toBe('2')
    engine.dispose()
  })

  it('does not send an action while its cancellation holds the same-tab lease', async () => {
    const db = await openOfflineDb(scope)
    await db.putAction({ ...input('cancel-race'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    const originalGetAction = db.getAction.bind(db)
    let entered!: () => void
    let release!: () => void
    const waiting = new Promise<void>(resolve => { entered = resolve })
    const held = new Promise<void>(resolve => { release = resolve })
    vi.spyOn(db, 'getAction').mockImplementation(async id => {
      if (id === 'cancel-race') { entered(); await held }
      return originalGetAction(id)
    })
    const sendBatch = vi.fn(async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }))
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch })

    const cancelling = engine.cancelAction('cancel-race')
    await waiting
    await engine.syncNow('event')
    release()
    await cancelling
    expect(sendBatch).not.toHaveBeenCalled()
    expect(await db.getAction('cancel-race')).toMatchObject({ state: 'cancelled' })
    engine.dispose()
  })

  it('retains the original media until upload acknowledgement', async () => {
    const db = await openOfflineDb(scope)
    let acknowledge!: () => void
    const acknowledged = new Promise<void>(resolve => { acknowledge = resolve })
    const uploadMedia = vi.fn(() => acknowledged)
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', uploadMedia, sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }) })
    const originalBlob = new Blob(['original'], { type: 'image/jpeg' })
    await engine.enqueueMedia({ id: 'photo-original', actionId: 'review', issueKey: 'TASK-1', name: 'robot.jpg', blob: new Blob(['webp'], { type: 'image/webp' }), originalBlob, mimeType: 'image/webp', sha256: 'a', sizeBytes: 4 })

    const syncing = engine.syncNow('upload')
    await vi.waitFor(async () => expect(await db.getMedia('photo-original')).toMatchObject({ state: 'uploading', originalBlob }))
    acknowledge()
    await syncing

    expect(await db.getMedia('photo-original')).toMatchObject({ state: 'confirmed', originalBlob: undefined })
    engine.dispose()
  })

  it('keeps IndexedDB open until an in-flight sync settles during disposal', async () => {
    const db = await openOfflineDb(scope)
    let acknowledge!: () => void
    const acknowledged = new Promise<void>(resolve => { acknowledge = resolve })
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone',
      uploadMedia: () => acknowledged,
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }),
    })
    await engine.enqueueMedia({ id: 'photo-disposal', actionId: 'review', issueKey: 'TASK-1', name: 'robot.jpg', blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 5 })
    const syncing = engine.syncNow('upload')
    await vi.waitFor(async () => expect(await db.getMedia('photo-disposal')).toMatchObject({ state: 'uploading' }))
    const close = vi.spyOn(db, 'close')
    engine.dispose()
    expect(close).not.toHaveBeenCalled()
    acknowledge()
    await syncing
    expect(close).toHaveBeenCalledTimes(1)
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

  it.each(['conflict', 'attention'] as const)('does not auto-send a migrated %s action after a late v1 ready write', async state => {
    const newScope = { ...scope, schema: 2 }
    const old = await openOfflineDb(scope)
    await old.putAction({ ...input('paused'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 1 })
    const current = await openOfflineDb(newScope)
    await current.putAction({ ...input('paused'), state, attempts: 1, createdAt: 1, updatedAt: 9 })
    const late = await openOfflineDb(scope)
    await late.putAction({ ...input('paused'), state: 'ready', attempts: 0, createdAt: 1, updatedAt: 20 })
    const reopened = await openOfflineDb(newScope)
    const sendBatch = vi.fn(async (batch: SyncBatchRequest) => ({
      results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })),
      deltas: {}, revisions: {}, revoked_scopes: [],
    }))
    const engine = new SyncEngine({ db: reopened, coordinator: coordinator(reopened), deviceId: 'phone', sendBatch })
    await engine.syncNow('reopen')
    expect(sendBatch).not.toHaveBeenCalled()
    expect(await reopened.getAction('paused')).toMatchObject({ state })
    if (state === 'conflict') {
      await engine.resolveConflict('paused', 'r2')
      await vi.waitFor(() => expect(sendBatch).toHaveBeenCalledOnce())
    }
    engine.dispose()
  })

  it.each([
    ['confirmed', 'conflict'], ['confirmed', 'attention'],
    ['cancelled', 'conflict'], ['cancelled', 'attention'],
  ] as const)('does not resend a final %s action or re-upload media after a late v1 %s write', async (destinationState, sourceState) => {
    const newScope = { ...scope, schema: 2 }
    const finalAction = { ...input('final'), state: destinationState, attempts: 1, createdAt: 1, updatedAt: 9 }
    const finalMedia = { id: 'photo', actionId: 'final', issueKey: 'TASK-1', name: 'final.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg', sha256: 'x', sizeBytes: 1, state: 'confirmed' as const, createdAt: 1, updatedAt: 9 }
    const current = await openOfflineDb(newScope)
    await current.putAction(finalAction)
    await current.putMedia(finalMedia)
    const late = await openOfflineDb(scope)
    await late.putAction({ ...finalAction, state: sourceState, updatedAt: 20 })
    await late.putMedia({ ...finalMedia, state: 'attention', updatedAt: 20 })
    const reopened = await openOfflineDb(newScope)
    const sendBatch = vi.fn(async (batch: SyncBatchRequest) => ({
      results: batch.actions.map(item => ({ client_action_id: item.client_action_id, state: 'confirmed' as const, code: null, result: null })),
      deltas: {}, revisions: {}, revoked_scopes: [],
    }))
    const uploadMedia = vi.fn(async () => {})
    const engine = new SyncEngine({ db: reopened, coordinator: coordinator(reopened), deviceId: 'phone', sendBatch, uploadMedia })
    try {
      await engine.syncNow('reopen')
      await engine.syncNow('retry')

      expect(sendBatch).not.toHaveBeenCalled()
      expect(uploadMedia).not.toHaveBeenCalled()
      expect(await reopened.getAction('final')).toMatchObject({ state: destinationState, updatedAt: 9 })
      expect(await reopened.getMedia('photo')).toMatchObject({ state: 'confirmed', updatedAt: 9 })
      expect(engine.getState()).toMatchObject({ pending: 0, conflicts: 0 })
    } finally {
      engine.dispose()
    }
  })

  it('exposes durable action state changes and finds an unresolved resource action', async () => {
    const sendBatch = vi.fn(async (batch: SyncBatchRequest) => ({
      results: [{ client_action_id: batch.actions[0].client_action_id, state: 'confirmed' as const, code: null, result: null }],
      deltas: {}, revisions: {}, revoked_scopes: [],
    }))
    const db = await openOfflineDb(scope)
    const engine = new SyncEngine({ db, coordinator: coordinator(db), deviceId: 'phone', sendBatch })
    const observed: string[] = []
    const stop = engine.subscribeAction('writeoff', action => { if (action) observed.push(action.state) })
    await engine.enqueueAction({ ...input('writeoff'), action: 'inventory_writeoff' })

    expect(await engine.findAction('TASK-1', 'inventory_writeoff')).toMatchObject({ id: 'writeoff', state: 'ready' })
    await engine.syncNow('test')

    expect(observed).toContain('ready')
    expect(observed.at(-1)).toBe('confirmed')
    expect(await engine.findAction('TASK-1', 'inventory_writeoff')).toBeUndefined()
    stop()
  })

  it('does not deliver a stale initial action read after a newer state notification', async () => {
    const db = await openOfflineDb(scope)
    let finishInitialRead!: (action: OfflineAction | undefined) => void
    vi.spyOn(db, 'getAction').mockReturnValueOnce(new Promise(resolve => { finishInitialRead = resolve }))
    const engine = new SyncEngine({
      db, coordinator: coordinator(db), deviceId: 'phone',
      sendBatch: async () => ({ results: [], deltas: {}, revisions: {}, revoked_scopes: [] }),
    })
    const observed: Array<string | undefined> = []
    engine.subscribeAction('writeoff', action => observed.push(action?.state))
    await engine.enqueueAction({ ...input('writeoff'), action: 'inventory_writeoff' })

    finishInitialRead(undefined)
    await Promise.resolve()

    expect(observed).toEqual(['ready'])
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
