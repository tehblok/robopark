import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useVisibilityPolling } from '../domains/robots/useVisibilityPolling'
import { resourceStore, useCachedResource } from './resource'
import { api } from '../api'

beforeEach(() => { vi.useFakeTimers(); vi.spyOn(Math, 'random').mockReturnValue(0.5) })
afterEach(() => { resourceStore.clearAll(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('loads robots immediately, then spreads ten-second polls and reconnects', async () => {
  const task = vi.fn(async () => {})
  const view = renderHook(({ online }) => useVisibilityPolling({ enabled: true, online, task }), { initialProps: { online: true } })
  await act(async () => {})
  expect(task).toHaveBeenCalledTimes(1)
  await act(() => vi.advanceTimersByTimeAsync(10_000))
  expect(task).toHaveBeenCalledTimes(1)
  await act(() => vi.advanceTimersByTimeAsync(5_000))
  expect(task).toHaveBeenCalledTimes(2)
  view.rerender({ online: false })
  await act(() => vi.advanceTimersByTimeAsync(60_000))
  view.rerender({ online: true })
  await act(async () => {})
  expect(task).toHaveBeenCalledTimes(2)
  await act(() => vi.advanceTimersByTimeAsync(15_000))
  expect(task).toHaveBeenCalledTimes(3)
})
it('spreads shared-resource polling and coalesces simultaneous resume events', async () => {
  const loader = vi.fn(async () => 'data')
  renderHook(() => useCachedResource('capacity', loader))
  await act(async () => {})
  await act(() => vi.advanceTimersByTimeAsync(30_000))
  expect(loader).toHaveBeenCalledTimes(1)
  await act(() => vi.advanceTimersByTimeAsync(15_000))
  expect(loader).toHaveBeenCalledTimes(2)
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  await act(() => vi.advanceTimersByTimeAsync(60_000))
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true)
  await act(async () => { window.dispatchEvent(new Event('online')); window.dispatchEvent(new Event('focus')) })
  expect(loader).toHaveBeenCalledTimes(2)
  await act(() => vi.advanceTimersByTimeAsync(15_000))
  expect(loader).toHaveBeenCalledTimes(3)
})
it('honors Retry-After across resume events without clearing cached data', async () => {
  const loader = vi.fn().mockResolvedValueOnce('cached').mockRejectedValue({ status: 429, retryAfterMs: 120_000 })
  const view = renderHook(() => useCachedResource('rate-limited', loader))
  await act(async () => {})
  await act(() => vi.advanceTimersByTimeAsync(45_000))
  expect(loader).toHaveBeenCalledTimes(2)
  await act(() => vi.advanceTimersByTimeAsync(60_000))
  await act(async () => window.dispatchEvent(new Event('focus')))
  await act(() => vi.advanceTimersByTimeAsync(15_000))
  expect(loader).toHaveBeenCalledTimes(2)
  expect(view.result.current.data).toBe('cached')
})
it.each(['120', 'Wed, 21 Oct 2026 07:28:00 GMT'])('carries Retry-After %s from a non-JSON proxy error', async header => {
  vi.setSystemTime(new Date('2026-10-21T07:26:00Z'))
  vi.stubGlobal('fetch', vi.fn(async () => new Response('rate limited', { status: 429, headers: { 'Retry-After': header } })))
  await expect(api.me()).rejects.toMatchObject({ status: 429, retryAfterMs: 120_000 })
})

it('distributes the first background refresh of 200 robot viewers across ten seconds', async () => {
  let sample = 0
  vi.spyOn(Math, 'random').mockImplementation(() => ((sample++ % 200) + 0.5) / 200)
  const start = Date.now()
  const requests: number[] = []
  for (let viewer = 0; viewer < 200; viewer += 1) {
    const task = async () => { requests.push(Date.now() - start) }
    renderHook(() => useVisibilityPolling({ enabled: true, online: true, task }))
  }
  await act(async () => {})
  expect(requests).toHaveLength(200) // cold loads remain immediate
  await act(() => vi.advanceTimersByTimeAsync(20_000))
  const background = requests.filter(time => time > 0)
  expect(background).toHaveLength(200)
  const busiestSecond = Math.max(...Array.from({ length: 10 }, (_, second) =>
    background.filter(time => time >= 10_000 + second * 1_000 && time < 11_000 + second * 1_000).length))
  expect(busiestSecond).toBeLessThanOrEqual(21) // at most 42 snapshot+section reads
})

it('does not let robot reconnect bypass a proxy Retry-After deadline', async () => {
  const task = vi.fn().mockRejectedValueOnce({ status: 429, retryAfterMs: 120_000 }).mockResolvedValue(undefined)
  const view = renderHook(({ online }) => useVisibilityPolling({ enabled: true, online, task }), { initialProps: { online: true } })
  await act(async () => {})
  view.rerender({ online: false })
  await act(() => vi.advanceTimersByTimeAsync(10_000))
  view.rerender({ online: true })
  await act(() => vi.advanceTimersByTimeAsync(109_999))
  expect(task).toHaveBeenCalledTimes(1)
  await act(() => vi.advanceTimersByTimeAsync(12_001))
  expect(task).toHaveBeenCalledTimes(2)
})
