import { IDBFactory, IDBObjectStore } from 'fake-indexeddb'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { IndexedResourceStore } from './indexedResourceStore'
import { resetDesktopStorageKeyForTests, setDesktopRuntimeForTests } from '../pwa/desktopRuntime'

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))
afterEach(() => { setDesktopRuntimeForTests(undefined); resetDesktopStorageKeyForTests(); vi.restoreAllMocks(); vi.useRealTimers() })

it('keeps the previous complete snapshot when a later batch write fails', async () => {
  const scope = { account: '7', role: 'mechanic', permissions: 'read', park: 'all', schema: 2 }
  const store = await IndexedResourceStore.open(scope)
  const batch = (value: string) => ['park:7', 'park:8', 'prepared:state'].map(key => ({ key, data: value, updatedAt: Date.now(), retentionMs: 72 * 60 * 60 * 1000 }))
  expect(await store.setBatch(batch('old'))).toBe(true)
  const original = IDBObjectStore.prototype.put
  let writes = 0
  const mock = vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (this: IDBObjectStore, ...args) {
    if (++writes === 2) throw new DOMException('full', 'QuotaExceededError')
    return original.apply(this, args as Parameters<IDBObjectStore['put']>)
  })
  expect(await store.setBatch(batch('new'))).toBe(false)
  mock.mockRestore()
  store.close()
  const reopened = await IndexedResourceStore.open(scope)
  for (const item of batch('old')) expect(await reopened.get(item.key)).toBe('old')
  reopened.close()
})

it('keeps encrypted snapshots when Keychain is temporarily unavailable', async () => {
  setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
  const key = btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify({ key })))
  const store = await IndexedResourceStore.open({ account: '7', role: 'mechanic', permissions: 'read', park: 'all', schema: 2 })
  await store.set('saved', { value: 'recoverable' })
  resetDesktopStorageKeyForTests()
  fetch.mockImplementation(async () => new Response('{}', { status: 503 }))
  expect(await store.get('saved')).toBeUndefined()
  fetch.mockImplementation(async () => new Response(JSON.stringify({ key })))
  expect(await store.get('saved')).toEqual({ value: 'recoverable' })
  store.close()
})

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

it('does not read a prior principals disk cache after a clean install reuses an account id', async () => {
  const previous = await IndexedResourceStore.open({ account: '7', principal: 'old-owner', role: 'royal', permissions: 'read', park: 'all', schema: 2 })
  await previous.set('system:summary', { value: 'previous install' })
  previous.close()

  const current = await IndexedResourceStore.open({ account: '7', principal: 'new-owner', role: 'royal', permissions: 'read', park: 'all', schema: 2 })
  expect(await current.get('system:summary')).toBeUndefined()
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

it('can retain an explicitly prepared offline snapshot beyond the default stale window', async () => {
  let now = new Date('2026-09-20T00:00:00Z').getTime()
  vi.spyOn(Date, 'now').mockImplementation(() => now)
  const scope = { account: '7', role: 'mechanic', permissions: 'read', park: '3', schema: 2 }
  const first = await IndexedResourceStore.open(scope)
  await first.set('prepared', ['offline'], first.captureGeneration(), Date.now(), 72 * 60 * 60 * 1000)
  first.close()
  now += 13 * 60 * 60 * 1000
  const retained = await IndexedResourceStore.open(scope)
  expect(await retained.get('prepared')).toEqual(['offline'])
  retained.close()
  now += 60 * 60 * 60 * 1000
  const expired = await IndexedResourceStore.open(scope)
  expect(await expired.get('prepared')).toBeUndefined()
  expired.close()
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
