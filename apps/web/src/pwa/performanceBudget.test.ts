import { describe, expect, it } from 'vitest'
import { MEDIA_CONCURRENCY, SYNC_BATCH_LIMIT, WEAK_LINK_BATCH_LIMIT } from './syncEngine'
import { estimateOfflineBudget } from './offlineDb'

describe('offline performance budgets', () => {
  it('keeps weak-link and normal synchronization bounded', () => {
    expect(WEAK_LINK_BATCH_LIMIT).toBe(2)
    expect(SYNC_BATCH_LIMIT).toBeLessThanOrEqual(20)
    expect(MEDIA_CONCURRENCY).toBeLessThanOrEqual(2)
  })

  it('uses only a bounded share of device storage', async () => {
    expect(await estimateOfflineBudget(async () => ({ quota: 1_000_000_000, usage: 100_000_000 }))).toBe(100_000_000)
    expect(await estimateOfflineBudget(async () => ({ quota: 10_000_000, usage: 0 }))).toBe(1_000_000)
    expect(await estimateOfflineBudget(async () => ({ quota: 1_000_000_000, usage: 990_000_000 }))).toBe(5_000_000)
    expect(await estimateOfflineBudget(async () => ({ quota: 10_000_000_000, usage: 0 }))).toBe(128 * 1024 * 1024)
  })
})
