import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import { beforeEach, expect, it, vi } from 'vitest'
import { IndexedResourceStore } from './indexedResourceStore'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))

it('isolates persisted resources by account, permissions, park and schema', async () => {
  const first = await IndexedResourceStore.open({ account: '7', role: 'mechanic', permissions: 'read', park: '3', schema: 2 })
  await first.set('work:list', { value: 'private' })
  expect(await first.get('work:list')).toEqual({ value: 'private' })
  expect(await (await IndexedResourceStore.open({ account: '7', role: 'mechanic', permissions: 'admin', park: '3', schema: 2 })).get('work:list')).toBeUndefined()
  expect(await (await IndexedResourceStore.open({ account: '8', role: 'mechanic', permissions: 'read', park: '3', schema: 2 })).get('work:list')).toBeUndefined()
})

it('does not read a previous role cache when permissions happen to match', async () => {
  const mechanic = await IndexedResourceStore.open({ account: '7', role: 'mechanic', permissions: 'read', park: '3', schema: 2 })
  await mechanic.set('work:list', { value: 'mechanic-only' })
  const manager = await IndexedResourceStore.open({ account: '7', role: 'manager', permissions: 'read', park: '3', schema: 2 })
  expect(await manager.get('work:list')).toBeUndefined()
})

it('survives reopening and purges the scope on authorization failure', async () => {
  const scope = { account: '7', role: 'mechanic', permissions: 'read', park: '3', schema: 2 }
  await (await IndexedResourceStore.open(scope)).set('work:list', ['cached'])
  expect(await (await IndexedResourceStore.open(scope)).get('work:list')).toEqual(['cached'])
  await (await IndexedResourceStore.open(scope)).purge()
  expect(await (await IndexedResourceStore.open(scope)).get('work:list')).toBeUndefined()
})

it('purges every older authorization scope when a new scope opens', async () => {
  const old = await IndexedResourceStore.open({ account: '7', role: 'manager', permissions: 'admin', park: 'all', schema: 1 })
  await old.set('privileged:list', ['secret'])
  old.close()

  const current = await IndexedResourceStore.open({ account: '7', role: 'mechanic', permissions: 'read', park: '3', schema: 1 })
  current.close()

  const reopenedOld = await IndexedResourceStore.open({ account: '7', role: 'manager', permissions: 'admin', park: 'all', schema: 1 })
  expect(await reopenedOld.get('privileged:list')).toBeUndefined()
})

it('preserves stored age through hydration reads', async () => {
  vi.setSystemTime(new Date('2026-09-20T00:00:00Z'))
  const store = await IndexedResourceStore.open(
    { account: '7', role: 'mechanic', permissions: 'read', park: '3', schema: 2 },
    { ttlMs: 1_000, staleMs: 10_000 },
  )
  const updatedAt = Date.now() - 3_000
  await store.set('work:list', ['stale'], store.captureGeneration(), updatedAt)
  expect(await store.getEntry('work:list')).toEqual({ data: ['stale'], updatedAt })
})

it('evicts least-recent entries on quota failure and never persists original photos', async () => {
  const scope = { account: '7', role: 'mechanic', permissions: 'read', park: '3', schema: 2 }
  const originalPut = IDBObjectStore.prototype.put
  let quotaFailures = 1
  vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (this: IDBObjectStore, ...args) {
    if (quotaFailures-- > 0) throw new DOMException('full', 'QuotaExceededError')
    return originalPut.apply(this, args as Parameters<IDBObjectStore['put']>)
  })
  const store = await IndexedResourceStore.open(scope, { maxEntries: 2, maxBytes: 1024 })
  await store.set('a', 'old')
  await store.set('b', 'new')
  await store.set('c', 'latest')
  await expect(store.set('photo:original:42', new Blob(['secret']))).rejects.toThrow(/original/i)
  expect((await store.stats()).entries).toBeLessThanOrEqual(2)
})
