import { describe, expect, it, vi } from 'vitest'
import { SyncCoordinator, type LeaseStore } from './syncCoordinator'

class MemoryLeaseStore implements LeaseStore {
  owner: string | null = null
  until = 0
  async claimLease(owner: string, now: number, leaseMs: number) {
    if (this.owner && this.owner !== owner && this.until > now) return false
    this.owner = owner; this.until = now + leaseMs
    return true
  }
  async releaseLease(owner: string) { if (this.owner === owner) { this.owner = null; this.until = 0 } }
}

describe('SyncCoordinator', () => {
  it('allows only one fallback leader at a time', async () => {
    const store = new MemoryLeaseStore()
    const first = new SyncCoordinator({ ownerId: 'one', leaseStore: store, now: () => 100 })
    const second = new SyncCoordinator({ ownerId: 'two', leaseStore: store, now: () => 100 })
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    const one = first.runExclusive(async () => { await gate })
    await Promise.resolve()

    expect(await second.runExclusive(vi.fn())).toBe(false)
    release()
    expect(await one).toBe(true)
  })

  it('takes over an expired fallback lease', async () => {
    const store = new MemoryLeaseStore()
    store.owner = 'dead'; store.until = 99
    const coordinator = new SyncCoordinator({ ownerId: 'live', leaseStore: store, now: () => 100 })
    const work = vi.fn()

    expect(await coordinator.runExclusive(work)).toBe(true)
    expect(work).toHaveBeenCalledOnce()
  })

  it('prefers Web Locks and skips work when the lock is unavailable', async () => {
    const request = vi.fn(async (_name, _options, callback) => callback(null))
    const coordinator = new SyncCoordinator({
      ownerId: 'tab', leaseStore: new MemoryLeaseStore(), lockManager: { request },
    })
    const work = vi.fn()

    expect(await coordinator.runExclusive(work)).toBe(false)
    expect(work).not.toHaveBeenCalled()
    expect(request).toHaveBeenCalledWith('robopark-sync', { ifAvailable: true }, expect.any(Function))
  })
})
