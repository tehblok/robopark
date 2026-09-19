import { beforeEach } from 'vitest'
import { createElement } from 'react'
import { act, cleanup, render, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  coalesceLoader,
  inFlight,
  resetCoalescingForTests,
  resourceStore,
  useCachedResource,
} from './resource'

type TestPayload = { value: string }

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function ResourceProbe({
  cacheKey,
  loader,
}: {
  cacheKey: string
  loader: () => Promise<TestPayload>
}) {
  useCachedResource(cacheKey, loader)
  return null
}

async function resolveAndFlush(
  request: ReturnType<typeof deferred<TestPayload>>,
  value: TestPayload,
) {
  await act(async () => {
    request.resolve(value)
    await request.promise
    await Promise.resolve()
    await Promise.resolve()
  })
}

describe('resourceStore', () => {
  afterEach(() => {
    resourceStore.clearAll()
  })

  it('returns memory values immediately', () => {
    resourceStore.set('now-report:all', { totals: { blocker: 3 } }, false)
    expect(resourceStore.get('now-report:all')).toEqual({ totals: { blocker: 3 } })
  })

  it('keeps settled data on route exit but drops it when access scope changes', () => {
    resourceStore.activateScope('work', 'work:7:access-a:')
    resourceStore.set('work:7:access-a:list', { value: 'ready' }, false)
    resourceStore.cancelPending('work:7:access-a:', { prefix: true })
    expect(resourceStore.get('work:7:access-a:list')).toEqual({ value: 'ready' })
    resourceStore.activateScope('work', 'work:7:access-b:')
    expect(resourceStore.get('work:7:access-a:list')).toBeUndefined()
  })

  it('releases old ticket responses during a long browser session', () => {
    for (let index = 0; index < 129; index += 1) {
      resourceStore.set(`tracker:issue:${index}`, { index }, false)
    }

    expect(resourceStore.get('tracker:issue:0')).toBeUndefined()
    expect(resourceStore.get('tracker:issue:128')).toEqual({ index: 128 })
  })

  it('notifies mounted screens when an evicted key is invalidated', () => {
    const listener = vi.fn()
    const unsubscribe = resourceStore.subscribe('work:7:issue:old', listener)
    resourceStore.set('work:7:issue:old', { value: 'stale' }, false)
    for (let index = 0; index < 128; index += 1) {
      resourceStore.set(`work:7:issue:${index}`, { index }, false)
    }
    listener.mockClear()

    resourceStore.invalidate('work:7:issue:', { prefix: true })

    expect(listener).toHaveBeenCalledOnce()
    expect(resourceStore.get('work:7:issue:old')).toBeUndefined()
    unsubscribe()
  })

  it('invalidates a single key without touching neighbors', () => {
    resourceStore.set('tracker:issue:A', { key: 'A' }, false)
    resourceStore.set('tracker:issue:B', { key: 'B' }, false)
    resourceStore.invalidate('tracker:issue:A')
    expect(resourceStore.get('tracker:issue:A')).toBeUndefined()
    expect(resourceStore.get('tracker:issue:B')).toEqual({ key: 'B' })
  })

  it('invalidates by prefix', () => {
    resourceStore.set('emergency:snapshot:VIN1', { vin: 'VIN1' }, false)
    resourceStore.set('emergency:section:VIN1:map', { id: 'map' }, false)
    resourceStore.set('now-report:all', { ok: true }, false)
    resourceStore.invalidate('emergency:', { prefix: true })
    expect(resourceStore.get('emergency:snapshot:VIN1')).toBeUndefined()
    expect(resourceStore.get('emergency:section:VIN1:map')).toBeUndefined()
    expect(resourceStore.get('now-report:all')).toEqual({ ok: true })
  })

  it('clearAll drops every entry', () => {
    resourceStore.set('operator:parks', [{ id: 1 }], false)
    resourceStore.clearAll()
    expect(resourceStore.get('operator:parks')).toBeUndefined()
  })

  it('ignores persisted entries older than the soft TTL', () => {
    const key = 'now-report:stale'
    window.localStorage.setItem(
      `robopark:res:${key}`,
      JSON.stringify({ v: 1, updatedAt: 1, data: { totals: { blocker: 99 } } }),
    )
    expect(resourceStore.get(key)).toBeUndefined()
    expect(window.localStorage.getItem(`robopark:res:${key}`)).toBeNull()
  })

  it('drops protected snapshots saved by the previous browser cache format', () => {
    const key = 'work:7:issue:OLD-1'
    window.localStorage.setItem(`robopark:res:${key}`, JSON.stringify({
      v: 1, updatedAt: Date.now(), data: { value: 'old private ticket' },
    }))

    expect(resourceStore.get(key)).toBeUndefined()
    expect(window.localStorage.getItem(`robopark:res:${key}`)).toBeNull()
  })

  it('does not hydrate a protected resource from a disk snapshot', async () => {
    const key = 'work:7:issue:DISK-1'
    localStorage.setItem(`robopark:res:${key}`, JSON.stringify({
      v: 2, updatedAt: Date.now(), data: { value: 'disk copy' },
    }))
    const loader = vi.fn(async () => ({ value: 'fresh from API' }))
    const view = renderHook(() => useCachedResource(key, loader))

    await waitFor(() => expect(view.result.current.data?.value).toBe('fresh from API'))
    expect(loader).toHaveBeenCalledOnce()
  })
})

describe('coalesceLoader', () => {
  afterEach(() => {
    resetCoalescingForTests()
  })

  it('shares one loader for the same key', async () => {
    let calls = 0
    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const loader = async () => {
      calls += 1
      await gate
      return { n: calls }
    }
    const first = coalesceLoader('now-report:all', loader)
    const second = coalesceLoader('now-report:all', loader)
    release()
    expect(await first).toEqual({ n: 1 })
    expect(await second).toEqual({ n: 1 })
    expect(calls).toBe(1)
  })

  it('does not share different keys', async () => {
    let calls = 0
    const loader = async () => {
      calls += 1
      return { n: calls }
    }
    await Promise.all([
      coalesceLoader('tracker:issue:A', loader),
      coalesceLoader('tracker:issue:B', loader),
    ])
    expect(calls).toBe(2)
  })

  it('does not talk to Startrek or Emergency from the browser', () => {
    expect(coalesceLoader.toString()).not.toMatch(/st-api\.yandex|tracker\.yandex|emergency\./i)
  })
})

describe('in-flight invalidation', () => {
  afterEach(() => {
    resourceStore.clearAll()
    resetCoalescingForTests()
  })

  it('does not publish a late hook result after prefix invalidation and unmount', async () => {
    const key = 'work:3:comments:ROBOPARK-42'
    const request = deferred<TestPayload>()
    const loader = vi.fn(() => request.promise)
    const view = render(createElement(ResourceProbe, { cacheKey: key, loader }))

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))
    resourceStore.invalidate('work:3:', { prefix: true })
    view.unmount()

    await resolveAndFlush(request, { value: 'late secret' })

    expect(resourceStore.get(key)).toBeUndefined()
    expect(window.localStorage.getItem(`robopark:res:${key}`)).toBeNull()
  })

  it('does not commit a late result after the owner unmounts', async () => {
    const key = 'slow'
    const request = deferred<TestPayload>()
    const loader = vi.fn(() => request.promise)
    const view = render(createElement(ResourceProbe, { cacheKey: key, loader }))

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))
    view.unmount()

    await resolveAndFlush(request, { value: 'late' })

    expect(resourceStore.get(key)).toBeUndefined()
    expect(inFlight.isActive).toBe(false)
  })

  it('starts a fresh same-key load after invalidation and discards the old result', async () => {
    const key = 'work:3:issue:ROBOPARK-42'
    const oldRequest = deferred<TestPayload>()
    const newRequest = deferred<TestPayload>()
    const oldLoader = vi.fn(() => oldRequest.promise)
    const newLoader = vi.fn(() => newRequest.promise)
    const oldView = render(
      createElement(ResourceProbe, { cacheKey: key, loader: oldLoader }),
    )

    await waitFor(() => expect(oldLoader).toHaveBeenCalledTimes(1))
    resourceStore.invalidate('work:3:', { prefix: true })
    oldView.unmount()

    const newView = render(
      createElement(ResourceProbe, { cacheKey: key, loader: newLoader }),
    )

    try {
      expect(newLoader).toHaveBeenCalledTimes(1)

      await resolveAndFlush(oldRequest, { value: 'stale' })
      expect(resourceStore.get(key)).toBeUndefined()

      await resolveAndFlush(newRequest, { value: 'fresh' })
      await waitFor(() => {
        expect(resourceStore.get(key)).toEqual({ value: 'fresh' })
      })
    } finally {
      oldRequest.resolve({ value: 'stale' })
      newRequest.resolve({ value: 'fresh' })
      newView.unmount()
    }
  })

  it('does not publish a late hook result after clearAll', async () => {
    const key = 'work:3:list:7'
    const request = deferred<TestPayload>()
    const loader = vi.fn(() => request.promise)
    const view = render(createElement(ResourceProbe, { cacheKey: key, loader }))

    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1))
    resourceStore.clearAll()
    view.unmount()

    await resolveAndFlush(request, { value: 'late secret' })

    expect(resourceStore.get(key)).toBeUndefined()
    expect(window.localStorage.getItem(`robopark:res:${key}`)).toBeNull()
  })
})

describe('automatic cached refresh', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    vi.restoreAllMocks()
    resourceStore.clearAll()
  })

  it('keeps a default private response in memory for instant return without writing localStorage', async () => {
    const loader = vi.fn(async () => ({ value: 'private ticket' }))
    const first = renderHook(() => useCachedResource('work:7:issue:ONE-1', loader))
    await waitFor(() => expect(first.result.current.data?.value).toBe('private ticket'))
    expect(localStorage.getItem('robopark:res:work:7:issue:ONE-1')).toBeNull()
    first.unmount()

    const second = renderHook(() => useCachedResource('work:7:issue:ONE-1', loader))
    expect(second.result.current.data?.value).toBe('private ticket')
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('refreshes a mounted resource after a change signal without hiding its last value', async () => {
    const pending = deferred<TestPayload>()
    let calls = 0
    const loader = vi.fn((): Promise<TestPayload> => ++calls === 1
      ? Promise.resolve({ value: 'last known' }) : pending.promise)
    const view = renderHook(() => useCachedResource('work:7:issue:ONE-1', loader))
    await waitFor(() => expect(view.result.current.data?.value).toBe('last known'))

    resourceStore.revalidate('work:7:', { prefix: true })
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2))
    expect(view.result.current.data?.value).toBe('last known')

    await resolveAndFlush(pending, { value: 'updated' })
    expect(view.result.current.data?.value).toBe('updated')
  })

  it('reuses a fresh cache and publishes a new value automatically when stale', async () => {
    vi.useFakeTimers()
    resourceStore.set('auto', { value: 'cached' }, false)
    const pending = deferred<TestPayload>()
    const loader = vi.fn(() => pending.promise)
    const view = renderHook(() => useCachedResource('auto', loader))
    expect(view.result.current.data?.value).toBe('cached')
    expect(loader).not.toHaveBeenCalled()
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(inFlight.isActive).toBe(false)
    expect(view.result.current.data?.value).toBe('cached')
    await resolveAndFlush(pending, { value: 'fresh' })
    expect(view.result.current.data?.value).toBe('fresh')
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('pauses hidden or offline and refreshes on return without duplicate focus requests', async () => {
    vi.useFakeTimers()
    let visible = true
    let online = true
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visible ? 'visible' : 'hidden')
    vi.spyOn(navigator, 'onLine', 'get').mockImplementation(() => online)
    const loader = vi.fn(async () => ({ value: String(loader.mock.calls.length) }))
    const view = renderHook(() => useCachedResource('paused', loader))
    await act(async () => {})
    visible = false
    await act(async () => document.dispatchEvent(new Event('visibilitychange')))
    expect(vi.getTimerCount()).toBe(0)
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(loader).toHaveBeenCalledTimes(1)
    visible = true
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('focus')) })
    expect(view.result.current.data?.value).toBe('2')
    expect(loader).toHaveBeenCalledTimes(2)
    online = false
    await act(() => vi.advanceTimersByTimeAsync(60_000))
    expect(loader).toHaveBeenCalledTimes(2)
    online = true
    await act(async () => window.dispatchEvent(new Event('online')))
    expect(view.result.current.data?.value).toBe('3')
  })

  it('coalesces mounted consumers and never overlaps slow polling', async () => {
    vi.useFakeTimers()
    const pending = deferred<TestPayload>()
    const loader = vi.fn(() => pending.promise)
    const first = renderHook(() => useCachedResource('shared', loader))
    const second = renderHook(() => useCachedResource('shared', loader))
    await act(() => vi.advanceTimersByTimeAsync(90_000))
    expect(loader).toHaveBeenCalledTimes(1)
    await resolveAndFlush(pending, { value: 'shared result' })
    expect(first.result.current.data?.value).toBe('shared result')
    expect(second.result.current.data?.value).toBe('shared result')
    first.unmount(); second.unmount()
    await act(() => vi.advanceTimersByTimeAsync(90_000))
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('backs off transient failures while preserving cached data', async () => {
    vi.useFakeTimers()
    vi.spyOn(Math, 'random').mockReturnValue(0)
    resourceStore.set('retry', { value: 'last known' }, false)
    const loader = vi.fn().mockRejectedValue(new Error('unavailable'))
    const view = renderHook(() => useCachedResource<TestPayload>('retry', loader))
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(loader).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(loader).toHaveBeenCalledTimes(2)
    await act(() => vi.advanceTimersByTimeAsync(30_000))
    expect(loader).toHaveBeenCalledTimes(2)
    expect(view.result.current.data?.value).toBe('last known')
    await act(() => vi.advanceTimersByTimeAsync(30_001))
    expect(loader).toHaveBeenCalledTimes(3)
  })

  it.each([401, 403])('halts automatic refresh after access error %s', async status => {
    vi.useFakeTimers()
    const loader = vi.fn().mockRejectedValue({ status })
    const first = renderHook(() => useCachedResource(`denied-${status}`, loader))
    const second = renderHook(() => useCachedResource(`denied-${status}`, loader))
    await act(async () => {})
    await act(() => vi.advanceTimersByTimeAsync(300_000))
    await act(async () => window.dispatchEvent(new Event('focus')))
    expect(first.result.current.error).toEqual({ status })
    expect(second.result.current.error).toEqual({ status })
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('loads a deferred offline cold resource on reconnect even when polling is disabled', async () => {
    vi.useFakeTimers()
    let online = false
    vi.spyOn(navigator, 'onLine', 'get').mockImplementation(() => online)
    const loader = vi.fn(async () => ({ value: 'initial form data' }))
    const view = renderHook(() => useCachedResource('cold-draft', loader, { refreshIntervalMs: 0 }))
    expect(loader).not.toHaveBeenCalled()
    online = true
    await act(async () => window.dispatchEvent(new Event('online')))
    expect(view.result.current.data?.value).toBe('initial form data')
    await act(() => vi.advanceTimersByTimeAsync(300_000))
    await act(async () => window.dispatchEvent(new Event('focus')))
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('allows forced mutation refresh and opts editable resources out of automatic replacement', async () => {
    vi.useFakeTimers()
    const loader = vi.fn(async () => ({ value: 'updated' }))
    resourceStore.set('draft', { value: 'original' }, false)
    const view = renderHook(() => useCachedResource('draft', loader, { refreshIntervalMs: 0 }))
    await act(() => vi.advanceTimersByTimeAsync(300_000))
    await act(async () => window.dispatchEvent(new Event('focus')))
    expect(loader).not.toHaveBeenCalled()
    await act(() => view.result.current.refresh())
    expect(view.result.current.data?.value).toBe('updated')
  })
})

describe('successful synchronization timestamp', () => {
  afterEach(() => { cleanup(); resourceStore.clearAll(); vi.restoreAllMocks() })

  it('keeps the last successful time on failure and clears it when access is denied', async () => {
    const loader = vi.fn().mockResolvedValue({ value: 'first' })
    const { result } = renderHook(() => useCachedResource('sync:owner', loader, { persist: false }))
    await waitFor(() => expect(result.current.data).toEqual({ value: 'first' }))
    const success = result.current.updatedAt
    expect(success).toEqual(expect.any(Number))
    loader.mockRejectedValueOnce(new Error('offline'))
    await act(async () => { await result.current.refresh() })
    expect(result.current.updatedAt).toBe(success)
    loader.mockRejectedValueOnce({ status: 403 })
    await act(async () => { await result.current.refresh() })
    expect(result.current.updatedAt).toBeNull()
    expect(result.current.data).toBeUndefined()
  })

  it('restores cache time without inventing a new synchronization', () => {
    const savedAt = Date.now() - 10_000
    localStorage.setItem('robopark:res:sync:cached', JSON.stringify({ v: 2, updatedAt: savedAt, data: { value: 'cached' } }))
    const loader = vi.fn()
    const { result, rerender } = renderHook(({ cacheKey }) => useCachedResource(cacheKey, loader, { enabled: cacheKey !== 'disabled', persist: true }), { initialProps: { cacheKey: 'sync:cached' } })
    expect(result.current.updatedAt).toBe(savedAt)
    expect(loader).not.toHaveBeenCalled()
    rerender({ cacheKey: 'disabled' })
    expect(result.current.updatedAt).toBeNull()
  })
})

// Existing lifecycle assertions use the minimum jitter; capacity tests cover dispersion.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })
