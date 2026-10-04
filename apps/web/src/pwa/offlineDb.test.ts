import { IDBFactory, IDBIndex as FakeIDBIndex } from 'fake-indexeddb'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  OFFLINE_DATABASE_NAME,
  OfflineStorageFullError,
  openOfflineDb,
  purgeOfflineScope,
} from './offlineDb'
import type { OfflineAction, OfflineScope } from './offlineTypes'

const scope = (account = '1', park = '1'): OfflineScope => ({
  account,
  role: 'mechanic',
  permissions: 'inventory.write,tracker.read',
  park,
  schema: 1,
})

const action = (id: string, state: OfflineAction['state'], updatedAt = 1): OfflineAction => ({
  id,
  deviceId: 'phone-1',
  resourceType: 'tracker_issue',
  resourceId: 'SDCFLEETOPS-1',
  action: 'comment',
  idempotencyKey: id,
  baseRevision: null,
  dependencies: [],
  payload: { text: id },
  state,
  attempts: 0,
  createdAt: updatedAt,
  updatedAt,
})

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))
afterEach(async () => {
  await purgeOfflineScope()
  vi.restoreAllMocks()
})

describe('offline database', () => {
  it('keeps a confirmed receipt when the same action is queued again and rejects changed content', async () => {
    const db = await openOfflineDb(scope())
    const completed = { ...action('one', 'confirmed'), result: { message_id: 42 } }
    await db.putAction(completed)

    expect(await db.putQueuedAction(action('one', 'ready', 2))).toMatchObject(completed)
    expect(await db.getAction('one')).toMatchObject(completed)
    await expect(db.putQueuedAction({ ...action('one', 'ready', 3), payload: { text: 'changed' } }))
      .rejects.toThrow('sync_payload_conflict')
    expect(await db.getAction('one')).toMatchObject(completed)
  })

  it('does not requeue confirmed media attached to a repeated action', async () => {
    const db = await openOfflineDb(scope())
    const completed = action('review', 'confirmed')
    const photo = {
      id: 'review-photo', actionId: 'review', issueKey: 'SDCFLEETOPS-1',
      name: 'robot.jpg', blob: new Blob(['photo']), mimeType: 'image/jpeg',
      sha256: 'photo-digest', sizeBytes: 5, createdAt: 1, updatedAt: 1,
      state: 'confirmed' as const,
    }
    await db.putAction(completed)
    await db.putMedia(photo)

    await db.putQueuedAction(action('review', 'ready', 2), { ...photo, state: 'ready', updatedAt: 2 })

    expect(await db.getAction('review')).toMatchObject({ state: 'confirmed' })
    expect(await db.getMedia('review-photo')).toMatchObject({ state: 'confirmed', updatedAt: 1 })
  })

  it('rejects a stale leader write in the same transaction that reads its lease', async () => {
    const db = await openOfflineDb(scope())
    await db.putAction(action('stale-leader', 'sending'))
    expect(await db.claimLease('tab-one', 0, 90)).toBe(true)
    expect(await db.renewLease('tab-one', 30, 90)).toBe(true)
    expect(await db.claimLease('tab-two', 121, 90)).toBe(true)

    const written = await db.transactionIfLease('tab-one', () => 121, writer => {
      writer.putAction(action('stale-leader', 'confirmed'))
    })

    expect(written).toBe(false)
    expect(await db.getAction('stale-leader')).toMatchObject({ state: 'sending' })
    expect(await db.renewLease('tab-two', 121, 90)).toBe(true)
  })

  it('migrates a legacy database and creates every required store and index', async () => {
    const legacy = indexedDB.open(OFFLINE_DATABASE_NAME, 1)
    legacy.onupgradeneeded = () => legacy.result.createObjectStore('entities', { keyPath: 'dbId' })
    await new Promise<void>((resolve, reject) => {
      legacy.onsuccess = () => { legacy.result.close(); resolve() }
      legacy.onerror = () => reject(legacy.error)
    })

    const db = await openOfflineDb(scope())

    expect(db.storeNames()).toEqual(['actions', 'entities', 'media', 'meta', 'revisions'])
    expect(db.indexNames('actions')).toEqual(['resource', 'scope', 'state', 'updatedAt'])
    expect(db.indexNames('media')).toEqual(['action', 'scope', 'state'])
  })

  it('preserves queued actions, media and entities when only the scope schema changes', async () => {
    const exactScope = { ...scope(), principal: 'alice', parkAccess: '1' }
    const previous = await openOfflineDb(exactScope)
    await previous.transaction(writer => {
      writer.putEntity('task:1', { title: 'pending' })
      writer.putAction(action('pending', 'ready'))
      writer.putMedia({ id: 'photo', actionId: 'pending', issueKey: 'TASK-1', name: 'a.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 1, state: 'local', createdAt: 1, updatedAt: 1 })
    })

    const next = await openOfflineDb({ ...exactScope, schema: 2 })

    expect(await next.getEntity('task:1')).toEqual({ title: 'pending' })
    expect(await next.getAction('pending')).toMatchObject({ state: 'ready' })
    expect(await next.getMedia('photo')).toMatchObject({ actionId: 'pending' })
    expect(await previous.getAction('pending')).toBeUndefined()
  })

  it('retains unproven legacy queued work without assigning it to a new principal', async () => {
    const setup = await openOfflineDb(scope())
    setup.close()
    const legacyScope = ['1', 'mechanic', 'inventory.write,tracker.read', '1', '1'].map(encodeURIComponent).join('|')
    const request = indexedDB.open(OFFLINE_DATABASE_NAME, 2)
    const raw = await new Promise<IDBDatabase>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    const transaction = raw.transaction('actions', 'readwrite')
    transaction.objectStore('actions').put({ ...action('legacy', 'ready'), dbId: `${legacyScope}\0legacy`, scope: legacyScope, bytes: 1, resource: 'tracker_issue:TASK-1' })
    await new Promise<void>((resolve, reject) => { transaction.oncomplete = () => resolve(); transaction.onerror = () => reject(transaction.error) })
    raw.close()

    const newPrincipal = await openOfflineDb({ ...scope(), principal: 'alice', parkAccess: '1', schema: 2 })
    expect(await newPrincipal.listActions()).toEqual([])
    const inspectRequest = indexedDB.open(OFFLINE_DATABASE_NAME, 2)
    const inspect = await new Promise<IDBDatabase>((resolve, reject) => {
      inspectRequest.onsuccess = () => resolve(inspectRequest.result)
      inspectRequest.onerror = () => reject(inspectRequest.error)
    })
    const stored = await new Promise<unknown>((resolve, reject) => {
      const get = inspect.transaction('actions').objectStore('actions').get(`${legacyScope}\0legacy`)
      get.onsuccess = () => resolve(get.result)
      get.onerror = () => reject(get.error)
    })
    expect(stored).toMatchObject({ id: 'legacy', state: 'ready' })
    inspect.close()
  })

  it('retains current projections for fourteen days by default', async () => {
    const db = await openOfflineDb(scope())
    const day = 24 * 60 * 60 * 1000
    await db.putEntity('task:recent', { current: true }, { updatedAt: 100 * day })

    await db.cleanup({ maxBytes: 1024, now: 113 * day })
    expect(await db.getEntity('task:recent')).toEqual({ current: true })

    await db.cleanup({ maxBytes: 1024, now: 114 * day })
    expect(await db.getEntity('task:recent')).toBeUndefined()
  })

  it('commits related entity, action and media writes atomically', async () => {
    const db = await openOfflineDb(scope())
    const pending = action('a-1', 'ready')
    const photo = new Blob(['photo'], { type: 'image/jpeg' })

    await db.transaction(writer => {
      writer.putEntity('task:1', { key: 'SDCFLEETOPS-1' })
      writer.putAction(pending)
      writer.putMedia({
        id: 'm-1', actionId: pending.id, issueKey: 'SDCFLEETOPS-1', name: 'robot.jpg', blob: photo, mimeType: photo.type,
        sha256: 'abc', sizeBytes: photo.size, state: 'local', createdAt: 1, updatedAt: 1,
      })
    })

    expect(await db.getEntity('task:1')).toEqual({ key: 'SDCFLEETOPS-1' })
    expect(await db.getAction('a-1')).toMatchObject({ state: 'ready' })
    expect(await db.getMedia('m-1')).toMatchObject({ actionId: 'a-1', state: 'local' })
  })

  it('rolls an entire transaction back when its writer throws', async () => {
    const db = await openOfflineDb(scope())

    await expect(db.transaction(writer => {
      writer.putEntity('task:1', { secret: true })
      writer.putAction(action('a-1', 'local'))
      throw new Error('stop')
    })).rejects.toThrow('stop')

    expect(await db.getEntity('task:1')).toBeUndefined()
    expect(await db.getAction('a-1')).toBeUndefined()
  })

  it('purges a previous authorization scope and rejects its late write', async () => {
    const previous = await openOfflineDb(scope('1', '1'))
    const generation = previous.captureGeneration()
    await previous.putEntity('task:secret', { secret: true })

    const current = await openOfflineDb(scope('2', '2'))
    const accepted = await previous.putEntity('task:late', { secret: true }, { generation })

    expect(accepted).toBe(false)
    expect(await current.getEntity('task:secret')).toBeUndefined()
    expect(await current.getEntity('task:late')).toBeUndefined()
  })

  it('keeps a previous scope durable but invisible until that exact scope is authorized again', async () => {
    const previous = await openOfflineDb(scope('1', '1'))
    await previous.putAction(action('pending-1', 'ready'))
    await previous.putMedia({ id: 'photo-1', actionId: 'pending-1', issueKey: 'TASK-1', name: 'a.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg', sha256: 'a', sizeBytes: 1, state: 'local', createdAt: 1, updatedAt: 1 })

    const next = await openOfflineDb(scope('2', '2'))
    expect(await next.listActions()).toEqual([])
    expect(await next.listMedia()).toEqual([])
    await purgeOfflineScope()
    expect(await previous.getAction('pending-1')).toBeUndefined()

    const restored = await openOfflineDb(scope('1', '1'))
    expect(await restored.getAction('pending-1')).toMatchObject({ state: 'ready' })
    expect(await restored.getMedia('photo-1')).toMatchObject({ state: 'local' })
  })

  it('isolates reused account IDs and changed park access by exact principal scope', async () => {
    const owner = { ...scope(), principal: 'old-account', parkAccess: '1,2' }
    const old = await openOfflineDb(owner)
    await old.putAction(action('private', 'ready'))
    await old.putEntity('private', { secret: true })
    const replacement = await openOfflineDb({ ...owner, principal: 'replacement-account' })
    expect(await replacement.listActions()).toEqual([])
    expect(await replacement.getEntity('private')).toBeUndefined()
    const narrowed = await openOfflineDb({ ...owner, parkAccess: '1' })
    expect(await narrowed.listActions()).toEqual([])
    const restored = await openOfflineDb(owner)
    expect(await restored.getAction('private')).toMatchObject({ state: 'ready' })
  })

  it('does not replace newer v2 action, entity, or media with late v1 copies', async () => {
    const old = await openOfflineDb(scope())
    await old.putAction(action('same', 'ready', 1))
    await old.putEntity('same', { version: 1 }, { updatedAt: 1 })
    await old.putMedia({ id: 'same', actionId: 'same', issueKey: 'TASK-1', name: 'old.jpg', blob: new Blob(['1']), mimeType: 'image/jpeg', sha256: 'old', sizeBytes: 1, state: 'local', createdAt: 1, updatedAt: 1 })
    const newScope = { ...scope(), schema: 2 }
    const current = await openOfflineDb(newScope)
    await current.putAction(action('same', 'confirmed', 9))
    await current.putEntity('same', { version: 2 }, { updatedAt: 9 })
    await current.putMedia({ id: 'same', actionId: 'same', issueKey: 'TASK-1', name: 'new.jpg', blob: new Blob(['2']), mimeType: 'image/jpeg', sha256: 'new', sizeBytes: 1, state: 'confirmed', createdAt: 9, updatedAt: 9 })
    const legacy = await openOfflineDb(scope())
    await legacy.putAction(action('same', 'ready', 20))
    await legacy.putEntity('same', { version: 1.5 }, { updatedAt: 2 })
    await legacy.putMedia({ id: 'same', actionId: 'same', issueKey: 'TASK-1', name: 'late-old.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg', sha256: 'late', sizeBytes: 1, state: 'local', createdAt: 20, updatedAt: 20 })
    const reopened = await openOfflineDb(newScope)
    expect(await reopened.getAction('same')).toMatchObject({ state: 'confirmed', updatedAt: 9 })
    expect(await reopened.getEntity('same')).toEqual({ version: 2 })
    expect(await reopened.getMedia('same')).toMatchObject({ name: 'new.jpg', updatedAt: 9 })
    await reopened.cleanup({ maxBytes: 1_000_000, now: 100, confirmedTtlMs: 10, entityTtlMs: 10 })
    const afterCleanup = await openOfflineDb(newScope)
    expect(await afterCleanup.getAction('same')).toBeUndefined()
    expect(await afterCleanup.getEntity('same')).toBeUndefined()
    expect(await afterCleanup.getMedia('same')).toBeUndefined()
  })

  it.each(['conflict', 'attention'] as const)('keeps a v2 %s action paused over a later v1 ready copy', async state => {
    const oldScope = scope()
    const newScope = { ...oldScope, schema: 2 }
    const original = await openOfflineDb(oldScope)
    await original.putAction(action('paused', 'ready', 1))
    const current = await openOfflineDb(newScope)
    await current.putAction(action('paused', state, 9))
    const late = await openOfflineDb(oldScope)
    await late.putAction(action('paused', 'ready', 20))

    const reopened = await openOfflineDb(newScope)
    expect(await reopened.getAction('paused')).toMatchObject({ state, updatedAt: 9 })
    const lower = await openOfflineDb(oldScope)
    expect(await lower.getAction('paused')).toBeUndefined()
  })

  it.each([
    ['confirmed', 'conflict'], ['confirmed', 'attention'],
    ['cancelled', 'conflict'], ['cancelled', 'attention'],
    ['confirmed', 'cancelled'], ['cancelled', 'confirmed'],
  ] as const)('keeps a v2 %s action over a later v1 %s copy', async (destinationState, sourceState) => {
    const newScope = { ...scope(), schema: 2 }
    const current = await openOfflineDb(newScope)
    await current.putAction(action('final', destinationState, 9))
    const late = await openOfflineDb(scope())
    await late.putAction(action('final', sourceState, 20))

    const reopened = await openOfflineDb(newScope)
    expect(await reopened.getAction('final')).toMatchObject({ state: destinationState, updatedAt: 9 })
    const lower = await openOfflineDb(scope())
    expect(await lower.getAction('final')).toBeUndefined()
  })

  it.each(['attention', 'confirmed'] as const)('keeps confirmed v2 media over a later v1 %s copy', async sourceState => {
    const newScope = { ...scope(), schema: 2 }
    const photo = { id: 'final', actionId: 'final', issueKey: 'TASK-1', name: 'new.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg', sha256: 'x', sizeBytes: 1, createdAt: 1 }
    const current = await openOfflineDb(newScope)
    await current.putMedia({ ...photo, state: 'confirmed', updatedAt: 9 })
    const late = await openOfflineDb(scope())
    await late.putMedia({ ...photo, name: 'old.jpg', state: sourceState, updatedAt: 20 })

    const reopened = await openOfflineDb(newScope)
    expect(await reopened.getMedia('final')).toMatchObject({ state: 'confirmed', name: 'new.jpg', updatedAt: 9 })
    const lower = await openOfflineDb(scope())
    expect(await lower.getMedia('final')).toBeUndefined()
  })

  it('preserves a fresh entity written by another connection during cleanup', async () => {
    const cleaning = await openOfflineDb(scope())
    const writing = await openOfflineDb(scope())
    await cleaning.putEntity('task:updated', { title: 'old' }, { updatedAt: 1 })
    const originalGetAll = FakeIDBIndex.prototype.getAll
    let concurrentWrite: Promise<boolean> | undefined
    let intercepted = false
    vi.spyOn(FakeIDBIndex.prototype, 'getAll').mockImplementation(function (this: IDBIndex, ...args) {
      const request = originalGetAll.apply(this, args)
      if (this.objectStore.name === 'entities' && !intercepted) {
        intercepted = true
        request.addEventListener('success', () => {
          concurrentWrite = writing.putEntity('task:updated', { title: 'fresh' }, { updatedAt: 10_000 })
        }, { once: true })
      }
      return request
    })

    await cleaning.cleanup({ maxBytes: 1_000_000, now: 10_000, entityTtlMs: 100 })
    expect(concurrentWrite).toBeDefined()
    expect(await concurrentWrite).toBe(true)
    expect(await writing.getEntity('task:updated')).toEqual({ title: 'fresh' })
  })

  it('cleans expired confirmed data but never removes pending actions or media', async () => {
    const db = await openOfflineDb(scope())
    await db.transaction(writer => {
      writer.putEntity('task:old', { value: 'old' }, { updatedAt: 1, accessedAt: 1 })
      writer.putAction(action('confirmed', 'confirmed', 1))
      writer.putAction(action('pending', 'ready', 1))
      writer.putMedia({
        id: 'waiting-photo', actionId: 'pending', issueKey: 'SDCFLEETOPS-1', name: 'robot.jpg', blob: new Blob(['x']), mimeType: 'image/jpeg',
        sha256: 'hash', sizeBytes: 1, state: 'local', createdAt: 1, updatedAt: 1,
      })
    })

    await expect(db.cleanup({ maxBytes: 0, now: 10_000, confirmedTtlMs: 100, entityTtlMs: 100 }))
      .rejects.toBeInstanceOf(OfflineStorageFullError)

    expect(await db.getEntity('task:old')).toBeUndefined()
    expect(await db.getAction('confirmed')).toBeUndefined()
    expect(await db.getAction('pending')).toMatchObject({ state: 'ready' })
    expect(await db.getMedia('waiting-photo')).toMatchObject({ state: 'local' })
  })

  it('retains acknowledged media until its dependent durable action is terminal', async () => {
    const db = await openOfflineDb(scope())
    const pendingReview = {
      ...action('review', 'ready', 1), action: 'submit_review', payload: { media_id: 'review-photo' },
    }
    await db.transaction(writer => {
      writer.putAction(pendingReview)
      writer.putMedia({
        id: 'review-photo', actionId: pendingReview.id, issueKey: 'SDCFLEETOPS-1', name: 'robot.jpg',
        blob: new Blob(['photo']), mimeType: 'image/jpeg', sha256: 'hash', sizeBytes: 5,
        state: 'confirmed', createdAt: 1, updatedAt: 1,
      })
    })

    await db.cleanup({ maxBytes: 1_000_000, now: 10_000, confirmedTtlMs: 100, entityTtlMs: 100 })
    expect(await db.getMedia('review-photo')).toMatchObject({ actionId: 'review', state: 'confirmed' })

    await db.putAction({ ...pendingReview, state: 'conflict', updatedAt: 1 })
    await db.cleanup({ maxBytes: 1_000_000, now: 20_000, confirmedTtlMs: 100, entityTtlMs: 100 })
    expect(await db.getMedia('review-photo')).toMatchObject({ actionId: 'review', state: 'confirmed' })

    await db.putAction({ ...pendingReview, state: 'confirmed', updatedAt: 19_950 })
    await db.cleanup({ maxBytes: 1_000_000, now: 20_000, confirmedTtlMs: 100, entityTtlMs: 100 })
    expect(await db.getMedia('review-photo')).toMatchObject({ state: 'confirmed' })

    await db.cleanup({ maxBytes: 1_000_000, now: 20_050, confirmedTtlMs: 100, entityTtlMs: 100 })
    expect(await db.getMedia('review-photo')).toBeUndefined()
  })

  it('retains a cancelled dependency tombstone while a review still references it', async () => {
    const db = await openOfflineDb(scope())
    await db.transaction(writer => {
      writer.putAction(action('cancelled-comment', 'cancelled'))
      writer.putAction({ ...action('pending-review', 'ready'), action: 'submit_review',
        dependencies: ['cancelled-comment'] })
    })

    await expect(db.cleanup({ maxBytes: 0, now: 10_000, confirmedTtlMs: 100, entityTtlMs: 100 }))
      .rejects.toBeInstanceOf(OfflineStorageFullError)
    expect(await db.getAction('cancelled-comment')).toMatchObject({ state: 'cancelled' })
    expect(await db.getAction('pending-review')).toMatchObject({ state: 'ready' })
  })

  it('retains a confirmed comment receipt while a queued review depends on it', async () => {
    const db = await openOfflineDb(scope())
    await db.transaction(writer => {
      writer.putAction(action('confirmed-comment', 'confirmed'))
      writer.putAction({ ...action('pending-review', 'ready'), action: 'submit_review',
        dependencies: ['confirmed-comment'] })
    })

    await db.cleanup({ maxBytes: 1_000_000, now: 10_000, confirmedTtlMs: 100, entityTtlMs: 100 })
    expect(await db.getAction('confirmed-comment')).toMatchObject({ state: 'confirmed' })
    await expect(db.cleanup({ maxBytes: 0, now: 10_000, confirmedTtlMs: 100, entityTtlMs: 100 }))
      .rejects.toBeInstanceOf(OfflineStorageFullError)
    expect(await db.getAction('confirmed-comment')).toMatchObject({ state: 'confirmed' })
  })

  it('stores and replaces section revisions inside the active scope', async () => {
    const db = await openOfflineDb(scope())
    await db.setRevision('tasks', 'r1')
    await db.setRevision('tasks', 'r2')

    expect(await db.getRevision('tasks')).toBe('r2')
  })
})
