import { afterEach, describe, expect, it, vi } from 'vitest'
import { ClientBatcher, OPTIMISTIC_BATCH_DELAY_MS } from './clientBatcher'

afterEach(() => vi.useRealTimers())

describe('ClientBatcher', () => {
  it('coalesces repeated schedules and flushes only after the optimistic delay', async () => {
    vi.useFakeTimers()
    const flush = vi.fn(async () => undefined)
    const batcher = new ClientBatcher(flush)

    batcher.schedule()
    batcher.schedule()
    await vi.advanceTimersByTimeAsync(OPTIMISTIC_BATCH_DELAY_MS - 1)
    expect(flush).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    expect(flush).toHaveBeenCalledOnce()
    batcher.dispose()
  })
})
