import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StrictMode, useLayoutEffect } from 'react'
import type { User } from '../api'
import {
  activateDeviceResourceCache,
  currentDeviceResourceCache,
  purgeDeviceResourceCache,
} from './deviceResourceCache'
import { resourceStore, useCachedResource } from './resource'

type Payload = { value: string }

const account: User = {
  id: 7,
  username: 'operator-seven',
  role: 'operator',
  access_status: 'approved',
  permissions: ['tracker.read'],
  parks: [],
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

function StrictScopeProbe({ loader }: { loader: () => Promise<Payload> }) {
  const resource = useCachedResource('work:strict:queue', loader, { refreshIntervalMs: 0 })
  useLayoutEffect(() => () => resourceStore.cancelPending('work:strict:', { prefix: true }), [])
  return <div>{resource.data?.value}</div>
}

describe('device resource hydration', () => {
  beforeEach(async () => {
    vi.stubGlobal('indexedDB', new IDBFactory())
    await activateDeviceResourceCache(account)
  })

  afterEach(async () => {
    cleanup()
    resourceStore.clearAll()
    await purgeDeviceResourceCache()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('loads from the network while a device-cache read remains pending', async () => {
    const store = currentDeviceResourceCache()!
    vi.spyOn(store, 'getEntry').mockImplementation(() => new Promise(() => undefined))
    const loader = vi.fn(async (): Promise<Payload> => ({ value: 'network' }))

    const view = renderHook(() => useCachedResource('work:queue', loader, { refreshIntervalMs: 0 }))

    await waitFor(() => expect(view.result.current.data).toEqual({ value: 'network' }))
    expect(view.result.current.isLoading).toBe(false)
  })

  it('starts one network request when StrictMode replays a scoped mount', async () => {
    const store = currentDeviceResourceCache()!
    vi.spyOn(store, 'getEntry').mockImplementation(() => new Promise(() => undefined))
    const response = deferred<Payload>()
    const loader = vi.fn(() => response.promise)

    render(<StrictMode><StrictScopeProbe loader={loader} /></StrictMode>)

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))
    await act(async () => {
      response.resolve({ value: 'network' })
      await response.promise
    })
    expect(screen.getByText('network')).toBeVisible()
  })

  it('ignores a failed device-cache read and keeps the network result', async () => {
    const store = currentDeviceResourceCache()!
    vi.spyOn(store, 'getEntry').mockRejectedValue(new Error('IndexedDB transaction failed'))
    const loader = vi.fn(async (): Promise<Payload> => ({ value: 'network' }))

    const view = renderHook(() => useCachedResource('work:failed-cache', loader, { refreshIntervalMs: 0 }))

    await waitFor(() => expect(view.result.current.data).toEqual({ value: 'network' }))
    expect(view.result.current.error).toBeNull()
  })

  it('does not let late device-cache data replace a fresh network response', async () => {
    const store = currentDeviceResourceCache()!
    const cached = deferred<{ data: Payload, updatedAt: number } | undefined>()
    vi.spyOn(store, 'getEntry').mockReturnValue(cached.promise)
    const loader = vi.fn(async (): Promise<Payload> => ({ value: 'fresh' }))
    const view = renderHook(() => useCachedResource('work:late-cache', loader, { refreshIntervalMs: 0 }))

    await waitFor(() => expect(view.result.current.data).toEqual({ value: 'fresh' }))
    await act(async () => {
      cached.resolve({ data: { value: 'stale' }, updatedAt: Date.now() - 1_000 })
      await cached.promise
      await Promise.resolve()
    })

    expect(view.result.current.data).toEqual({ value: 'fresh' })
    expect(resourceStore.get('work:late-cache')).toEqual({ value: 'fresh' })
  })

  it('does not restore late device-cache data after access is revoked', async () => {
    const store = currentDeviceResourceCache()!
    const cached = deferred<{ data: Payload, updatedAt: number } | undefined>()
    vi.spyOn(store, 'getEntry').mockReturnValue(cached.promise)
    const loader = vi.fn(async (): Promise<Payload> => {
      resourceStore.clearAll()
      throw { status: 403 }
    })
    const view = renderHook(() => useCachedResource('work:revoked-cache', loader, { refreshIntervalMs: 0 }))

    await waitFor(() => expect(view.result.current.error).toEqual({ status: 403 }))
    await act(async () => {
      cached.resolve({ data: { value: 'denied' }, updatedAt: Date.now() })
      await cached.promise
      await Promise.resolve()
    })

    expect(view.result.current.data).toBeUndefined()
    expect(resourceStore.get('work:revoked-cache')).toBeUndefined()
  })
})
