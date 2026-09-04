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
it('backs off, pauses hidden, resumes immediately and resets after success', async () => {
  const task = vi.fn().mockRejectedValueOnce(Error()).mockRejectedValueOnce(Error()).mockResolvedValue(undefined)
  render(<Probe task={task} />); await flush()
  expect(task).toHaveBeenCalledTimes(1)
  await act(async () => vi.advanceTimersByTimeAsync(2500)); expect(task).toHaveBeenCalledTimes(2)
  await act(async () => vi.advanceTimersByTimeAsync(4999)); expect(task).toHaveBeenCalledTimes(2)
  Object.defineProperty(document, 'hidden', { value: true }); fireEvent(document, new Event('visibilitychange'))
  await act(async () => vi.advanceTimersByTimeAsync(30000)); expect(task).toHaveBeenCalledTimes(2)
  Object.defineProperty(document, 'hidden', { value: false }); fireEvent(document, new Event('visibilitychange')); await flush()
  expect(task).toHaveBeenCalledTimes(3)
  await act(async () => vi.advanceTimersByTimeAsync(2500)); expect(task).toHaveBeenCalledTimes(4)
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
it('new task identity does not coalesce with or inherit old completion', async () => {
  let done!: () => void
  const old = vi.fn(() => new Promise<void>(resolve => { done = resolve }))
  const next = vi.fn(async () => undefined)
  const view = render(<Probe task={old} />); await flush()
  view.rerender(<Probe task={next} />); await flush(); expect(next).toHaveBeenCalledTimes(1)
  await act(async () => done())
  await act(async () => vi.advanceTimersByTimeAsync(2500)); expect(next).toHaveBeenCalledTimes(2)
})
it('StrictMode cleanup rejects old ownership and unmount clears scheduling', async () => {
  const task = vi.fn(async () => undefined)
  const view = render(<StrictMode><Probe task={task} /></StrictMode>); await flush()
  const initial = task.mock.calls.length
  await act(async () => vi.advanceTimersByTimeAsync(2500)); expect(task).toHaveBeenCalledTimes(initial + 1)
  view.unmount(); await act(async () => vi.advanceTimersByTimeAsync(30000)); expect(task).toHaveBeenCalledTimes(initial + 1)
})
