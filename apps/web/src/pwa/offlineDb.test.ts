import { IDBFactory } from 'fake-indexeddb'
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

  it('commits related entity, action and media writes atomically', async () => {
    const db = await openOfflineDb(scope())
    const pending = action('a-1', 'ready')
    const photo = new Blob(['photo'], { type: 'image/jpeg' })

    await db.transaction(writer => {
      writer.putEntity('task:1', { key: 'SDCFLEETOPS-1' })
      writer.putAction(pending)
      writer.putMedia({
        id: 'm-1', actionId: pending.id, blob: photo, mimeType: photo.type,
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

  it('cleans expired confirmed data but never removes pending actions or media', async () => {
    const db = await openOfflineDb(scope())
    await db.transaction(writer => {
      writer.putEntity('task:old', { value: 'old' }, { updatedAt: 1, accessedAt: 1 })
      writer.putAction(action('confirmed', 'confirmed', 1))
      writer.putAction(action('pending', 'ready', 1))
      writer.putMedia({
        id: 'waiting-photo', actionId: 'pending', blob: new Blob(['x']), mimeType: 'image/jpeg',
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

  it('stores and replaces section revisions inside the active scope', async () => {
    const db = await openOfflineDb(scope())
    await db.setRevision('tasks', 'r1')
    await db.setRevision('tasks', 'r2')

    expect(await db.getRevision('tasks')).toBe('r2')
  })
})
