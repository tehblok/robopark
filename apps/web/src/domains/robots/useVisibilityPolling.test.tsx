import { StrictMode } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useVisibilityPolling } from './useVisibilityPolling'
function Probe({ task, online = true, enabled = true }: { task: () => Promise<void>; online?: boolean; enabled?: boolean }) {
  const { pending, refreshNow } = useVisibilityPolling({ enabled, online, task })
  return <button onClick={() => void refreshNow()}>{pending ? 'pending' : 'refresh'}</button>
}
beforeEach(() => { vi.useFakeTimers(); Object.defineProperty(document, 'hidden', { configurable: true, value: false }) })
afterEach(() => { vi.useRealTimers(); Object.defineProperty(document, 'hidden', { configurable: true, value: false }) })
const flush = () => act(async () => undefined)
it('backs off, pauses hidden, resumes with jitter and resets after success', async () => {
  const task = vi.fn().mockRejectedValueOnce(Error()).mockRejectedValueOnce(Error()).mockResolvedValue(undefined)
  render(<Probe task={task} />); await flush()
  expect(task).toHaveBeenCalledTimes(1)
  await act(async () => vi.advanceTimersByTimeAsync(10_000)); expect(task).toHaveBeenCalledTimes(2)
  await act(async () => vi.advanceTimersByTimeAsync(19_999)); expect(task).toHaveBeenCalledTimes(2)
  Object.defineProperty(document, 'hidden', { value: true }); fireEvent(document, new Event('visibilitychange'))
  await act(async () => vi.advanceTimersByTimeAsync(30000)); expect(task).toHaveBeenCalledTimes(2)
  Object.defineProperty(document, 'hidden', { value: false }); fireEvent(document, new Event('visibilitychange')); await flush()
  expect(task).toHaveBeenCalledTimes(3)
  await act(async () => vi.advanceTimersByTimeAsync(10_000)); expect(task).toHaveBeenCalledTimes(4)
})
it('allows manual offline/hidden work, coalesces, but refuses disabled work', async () => {
  let done!: () => void
  const task = vi.fn(() => new Promise<void>(resolve => { done = resolve }))
  const view = render(<Probe task={task} online={false} />); await flush(); expect(task).not.toHaveBeenCalled()
  Object.defineProperty(document, 'hidden', { value: true })
  fireEvent.click(screen.getByRole('button')); fireEvent.click(screen.getByRole('button')); await flush()
  expect(task).toHaveBeenCalledTimes(1)
  await act(async () => done()); expect(screen.getByRole('button')).toHaveTextContent('refresh')
  view.rerender(<Probe task={task} enabled={false} />)
  fireEvent.click(screen.getByRole('button')); await flush(); expect(task).toHaveBeenCalledTimes(1)
})
it('recovers an offline robot poll immediately at the minimum reconnect jitter', async () => {
  const task = vi.fn(async () => undefined)
  const view = render(<Probe task={task} online={false} />)
  await flush()
  expect(task).not.toHaveBeenCalled()

  view.rerender(<Probe task={task} online />)
  await flush()

  expect(task).toHaveBeenCalledTimes(1)
})

it('recovers an offline robot poll before the maximum reconnect jitter elapses', async () => {
  vi.spyOn(Math, 'random').mockReturnValue(0.999999)
  const task = vi.fn(async () => undefined)
  const view = render(<Probe task={task} online={false} />)
  await flush()
  expect(task).not.toHaveBeenCalled()

  view.rerender(<Probe task={task} online />)
  await act(async () => vi.advanceTimersByTimeAsync(29_998))
  expect(task).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTimeAsync(1))
  expect(task).toHaveBeenCalledTimes(1)
})

it('new task identity does not coalesce with or inherit old completion', async () => {
  let done!: () => void
  const old = vi.fn(() => new Promise<void>(resolve => { done = resolve }))
  const next = vi.fn(async () => undefined)
  const view = render(<Probe task={old} />); await flush()
  view.rerender(<Probe task={next} />); await flush(); expect(next).toHaveBeenCalledTimes(1)
  await act(async () => done())
  await act(async () => vi.advanceTimersByTimeAsync(10_000)); expect(next).toHaveBeenCalledTimes(2)
})
it('StrictMode cleanup rejects old ownership and unmount clears scheduling', async () => {
  const task = vi.fn(async () => undefined)
  const view = render(<StrictMode><Probe task={task} /></StrictMode>); await flush()
  const initial = task.mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(10_000)); expect(task).toHaveBeenCalledTimes(initial + 1)
  view.unmount(); await act(async () => vi.advanceTimersByTimeAsync(30000)); expect(task).toHaveBeenCalledTimes(initial + 1)
})

it('rechecks visibility before starting a queued automatic request', async () => {
  const task = vi.fn(async () => undefined)
  render(<Probe task={task} />)
  Object.defineProperty(document, 'hidden', { value: true })
  fireEvent(document, new Event('visibilitychange'))
  await flush()
  expect(task).not.toHaveBeenCalled()
})
it('resumes immediately when a failed hidden tab becomes visible and still keeps one request in flight', async () => {
  let release!: () => void
  const task = vi.fn()
    .mockRejectedValueOnce(Error('offline'))
    .mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve }))
  render(<Probe task={task} />)
  await flush()
  Object.defineProperty(document, 'hidden', { value: true })
  fireEvent(document, new Event('visibilitychange'))
  Object.defineProperty(document, 'hidden', { value: false })
  fireEvent(document, new Event('visibilitychange'))
  await flush()
  expect(task).toHaveBeenCalledTimes(2)
  fireEvent.click(screen.getByRole('button'))
  await flush()
  expect(task).toHaveBeenCalledTimes(2)
  await act(async () => release())
})

it('keeps a server Retry-After deadline when a hidden tab becomes visible', async () => {
  const task = vi.fn()
    .mockRejectedValueOnce({ status: 429, retryAfterMs: 120_000 })
    .mockResolvedValue(undefined)
  render(<Probe task={task} />)
  await flush()
  Object.defineProperty(document, 'hidden', { value: true })
  fireEvent(document, new Event('visibilitychange'))
  fireEvent.click(screen.getByRole('button'))
  await flush()
  expect(task).toHaveBeenCalledTimes(1)
  await act(async () => vi.advanceTimersByTimeAsync(30_000))
  Object.defineProperty(document, 'hidden', { value: false })
  fireEvent(document, new Event('visibilitychange'))
  await flush()
  expect(task).toHaveBeenCalledTimes(1)
  await act(async () => vi.advanceTimersByTimeAsync(89_999))
  expect(task).toHaveBeenCalledTimes(1)
  await act(async () => vi.advanceTimersByTimeAsync(1))
  expect(task).toHaveBeenCalledTimes(2)
})

it('keeps a server Retry-After deadline when the diagnostic tab changes', async () => {
  const limited = vi.fn().mockRejectedValue({ status: 429, retryAfterMs: 120_000 })
  const nextTab = vi.fn().mockResolvedValue(undefined)
  const view = render(<Probe task={limited} />)
  await flush()
  view.rerender(<Probe task={nextTab} />)
  await flush()
  expect(nextTab).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTimeAsync(120_000))
  expect(nextTab).toHaveBeenCalledTimes(1)
})

// Existing lifecycle assertions use the minimum jitter; capacity tests cover dispersion.
beforeEach(() => { vi.spyOn(Math, 'random').mockReturnValue(0) })
