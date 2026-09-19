import { afterEach, expect, it, vi } from 'vitest'
import { resetChangeFeedVersionsForTests, startChangeFeed } from './changeFeed'

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); resetChangeFeedVersionsForTests() })

it('catches a change made while the route was unmounted', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  let revision = 1
  const load = vi.fn(async () => revision)
  const onChange = vi.fn()
  const first = startChangeFeed({ identity: 'user-7', scope: 'work:mine', load, onChange })
  await vi.advanceTimersByTimeAsync(0)
  first()
  revision = 2
  const second = startChangeFeed({ identity: 'user-7', scope: 'work:mine', load, onChange })
  await vi.advanceTimersByTimeAsync(0)
  expect(onChange).toHaveBeenCalledOnce()
  second()
})

it('includes request latency in the check cadence', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  const load = vi.fn(async () => {
    await new Promise(resolve => window.setTimeout(resolve, 700))
    return 1
  })
  const stop = startChangeFeed({ scope: 'work', load, onChange: vi.fn() })
  await vi.advanceTimersByTimeAsync(3_999)
  expect(load).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1)
  expect(load).toHaveBeenCalledTimes(2)
  stop()
})

it('notifies once when a version changes and stops after cleanup', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  let revision = 3
  const load = vi.fn(async () => revision)
  const onChange = vi.fn()
  const stop = startChangeFeed({ scope: 'work', load, onChange })

  await vi.advanceTimersByTimeAsync(0)
  expect(load).toHaveBeenCalledTimes(1)
  expect(onChange).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(5_000)
  expect(onChange).not.toHaveBeenCalled()
  revision = 4
  await vi.advanceTimersByTimeAsync(5_000)
  expect(onChange).toHaveBeenCalledOnce()

  stop()
  await vi.advanceTimersByTimeAsync(30_000)
  expect(load).toHaveBeenCalledTimes(3)
})

it('does not poll a hidden tab and resumes when it becomes visible', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  let hidden = true
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
  const load = vi.fn(async () => 1)
  const stop = startChangeFeed({ scope: 'work', load, onChange: vi.fn() })

  await vi.advanceTimersByTimeAsync(30_000)
  expect(load).not.toHaveBeenCalled()
  hidden = false
  document.dispatchEvent(new Event('visibilitychange'))
  await vi.advanceTimersByTimeAsync(0)
  expect(load).toHaveBeenCalledOnce()
  stop()
})

it('stops on revoked access instead of retrying with stale credentials', async () => {
  vi.useFakeTimers()
  const load = vi.fn().mockRejectedValue({ status: 403 })
  const onAuthorizationFailure = vi.fn()
  const stop = startChangeFeed({ scope: 'work', load, onChange: vi.fn(), onAuthorizationFailure })

  await vi.advanceTimersByTimeAsync(60_000)
  expect(load).toHaveBeenCalledOnce()
  expect(onAuthorizationFailure).toHaveBeenCalledOnce()
  stop()
})

it('honors Retry-After across focus and online resume events', async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  const load = vi.fn()
    .mockRejectedValueOnce({ status: 429, retryAfterMs: 20_000 })
    .mockResolvedValue(1)
  const stop = startChangeFeed({ scope: 'work', load, onChange: vi.fn() })
  await vi.advanceTimersByTimeAsync(0)
  window.dispatchEvent(new Event('online'))
  document.dispatchEvent(new Event('visibilitychange'))
  await vi.advanceTimersByTimeAsync(19_999)
  expect(load).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(1)
  expect(load).toHaveBeenCalledTimes(2)
  stop()
})
