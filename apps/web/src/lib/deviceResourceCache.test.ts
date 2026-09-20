import { IDBFactory } from 'fake-indexeddb'
import { waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { User } from '../api'
import {
  activateDeviceResourceCache,
  currentDeviceResourceCache,
  purgeDeviceResourceCache,
} from './deviceResourceCache'
import { IndexedResourceStore } from './indexedResourceStore'
import { resourceStore } from './resource'
import { offlineScopeForUser } from './deviceResourceCache'

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

it('falls back to memory-only caching when IndexedDB cannot be opened', async () => {
  vi.spyOn(IndexedResourceStore, 'open').mockRejectedValueOnce(new DOMException('blocked', 'SecurityError'))

  await expect(activateDeviceResourceCache(user(1))).resolves.toBeUndefined()

  expect(currentDeviceResourceCache()).toBeNull()
})

it('does not block the next account when purging the previous disk cache fails', async () => {
  await activateDeviceResourceCache(user(1))
  const previous = currentDeviceResourceCache()!
  vi.spyOn(previous, 'purge').mockRejectedValueOnce(new Error('transaction failed'))

  await expect(activateDeviceResourceCache(user(2))).resolves.toBeUndefined()

  expect(currentDeviceResourceCache()).not.toBe(previous)
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

it('does not let a pending hydrate overwrite a newer value for the same key', async () => {
  await activateDeviceResourceCache(user(1))
  const store = currentDeviceResourceCache()!
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  const staleRead = vi.spyOn(store, 'getEntry').mockImplementation(async () => {
    await gate
    return { data: { value: 'old' }, updatedAt: Date.now() - 1_000 }
  })
  const listener = vi.fn()
  const unsubscribe = resourceStore.subscribe('tracker:item', listener)

  const hydration = resourceStore.hydrate('tracker:item')
  resourceStore.set('tracker:item', { value: 'new' }, false)
  release()
  await hydration

  expect(resourceStore.get('tracker:item')).toEqual({ value: 'new' })
  expect(listener).toHaveBeenCalledTimes(1)
  staleRead.mockRestore()
  await waitFor(async () => expect(await store.get('tracker:item')).toEqual({ value: 'new' }))
  unsubscribe()
})

it('does not notify or restore a key when it is invalidated during hydration', async () => {
  await activateDeviceResourceCache(user(1))
  const store = currentDeviceResourceCache()!
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  vi.spyOn(store, 'getEntry').mockImplementation(async () => {
    await gate
    return { data: { value: 'old' }, updatedAt: Date.now() }
  })
  const listener = vi.fn()
  const unsubscribe = resourceStore.subscribe('tracker:item', listener)

  const hydration = resourceStore.hydrate('tracker:item')
  resourceStore.invalidate('tracker:item')
  release()
  await hydration

  expect(resourceStore.get('tracker:item')).toBeUndefined()
  expect(listener).toHaveBeenCalledTimes(1)
  unsubscribe()
})

it('builds the same stable authorization scope regardless of permission or park order', () => {
  const left = user(7, 'mechanic')
  left.permissions = ['tracker.read', 'inventory.write']
  left.parks = [{ id: 2, name: 'Two', tag: 'two' }, { id: 1, name: 'One', tag: 'one' }]
  const right = { ...left, permissions: [...left.permissions].reverse(), parks: [...left.parks].reverse() }

  expect(offlineScopeForUser(left, '2')).toEqual(offlineScopeForUser(right, '2'))
  expect(offlineScopeForUser(left, '2')).toMatchObject({
    account: '7', role: 'mechanic', park: '2', schema: 1,
  })
})
