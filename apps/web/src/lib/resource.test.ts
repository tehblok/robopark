import { createElement } from 'react'
import { act, render, waitFor } from '@testing-library/react'
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
