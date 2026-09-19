import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { User } from '../api'
import {
  activateDeviceResourceCache,
  currentDeviceResourceCache,
  purgeDeviceResourceCache,
} from './deviceResourceCache'
import { resourceStore } from './resource'

const user = (id: number, role = 'operator'): User => ({
  id, username: `user-${id}`, role, access_status: 'approved', permissions: ['tracker.read'], parks: [],
})

beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))
afterEach(async () => {
  await purgeDeviceResourceCache()
  resourceStore.clearAll()
  vi.restoreAllMocks()
})

it('ignores a late IndexedDB read after authorization purge', async () => {
  await activateDeviceResourceCache(user(1))
  const store = currentDeviceResourceCache()!
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  vi.spyOn(store, 'getEntry').mockImplementation(async () => {
    await gate
    return { data: { secret: true }, updatedAt: Date.now() }
  })

  const hydration = resourceStore.hydrate('tracker:secret')
  const purge = purgeDeviceResourceCache()
  release()
  await Promise.all([hydration, purge])

  expect(resourceStore.get('tracker:secret')).toBeUndefined()
})

it('preserves disk age so a stale hydration remains stale', async () => {
  await activateDeviceResourceCache(user(1))
  const store = currentDeviceResourceCache()!
  const updatedAt = Date.now() - 60_000
  await store.set('tracker:list', ['cached'], store.captureGeneration(), updatedAt)
  await resourceStore.hydrate('tracker:list')

  expect(resourceStore.updatedAt('tracker:list')).toBe(updatedAt)
  expect(resourceStore.isStale('tracker:list', 30_000)).toBe(true)
})
