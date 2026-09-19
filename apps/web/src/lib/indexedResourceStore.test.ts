import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import { beforeEach, expect, it, vi } from 'vitest'
import { IndexedResourceStore } from './indexedResourceStore'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))

it('isolates persisted resources by account, permissions, park and schema', async () => {
  const first = await IndexedResourceStore.open({ account: '7', permissions: 'read', park: '3', schema: 2 })
  await first.set('work:list', { value: 'private' })
  expect(await first.get('work:list')).toEqual({ value: 'private' })
  expect(await (await IndexedResourceStore.open({ account: '7', permissions: 'admin', park: '3', schema: 2 })).get('work:list')).toBeUndefined()
  expect(await (await IndexedResourceStore.open({ account: '8', permissions: 'read', park: '3', schema: 2 })).get('work:list')).toBeUndefined()
})

it('survives reopening and purges the scope on authorization failure', async () => {
  const scope = { account: '7', permissions: 'read', park: '3', schema: 2 }
  await (await IndexedResourceStore.open(scope)).set('work:list', ['cached'])
  expect(await (await IndexedResourceStore.open(scope)).get('work:list')).toEqual(['cached'])
  await (await IndexedResourceStore.open(scope)).purge()
  expect(await (await IndexedResourceStore.open(scope)).get('work:list')).toBeUndefined()
})

it('evicts least-recent entries on quota failure and never persists original photos', async () => {
  const scope = { account: '7', permissions: 'read', park: '3', schema: 2 }
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
