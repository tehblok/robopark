export type LeaseStore = {
  claimLease(owner: string, now: number, leaseMs: number): Promise<boolean>
  releaseLease(owner: string): Promise<void>
}

type LockManagerLike = {
  request<T>(
    name: string,
    options: { ifAvailable: true },
    callback: (lock: unknown | null) => Promise<T> | T,
  ): Promise<T>
}

export class SyncCoordinator {
  private readonly ownerId: string
  private readonly leaseStore: LeaseStore
  private readonly lockManager?: LockManagerLike
  private readonly now: () => number
  private readonly leaseMs: number

  constructor(options: {
    ownerId: string
    leaseStore: LeaseStore
    lockManager?: LockManagerLike
    now?: () => number
    leaseMs?: number
  }) {
    this.ownerId = options.ownerId
    this.leaseStore = options.leaseStore
    this.lockManager = options.lockManager
    this.now = options.now ?? Date.now
    this.leaseMs = options.leaseMs ?? 15_000
  }

  async runExclusive(work: () => Promise<void> | void): Promise<boolean> {
    if (this.lockManager) {
      return this.lockManager.request('robopark-sync', { ifAvailable: true }, async lock => {
        if (!lock) return false
        await work()
        return true
      })
    }
    if (!await this.leaseStore.claimLease(this.ownerId, this.now(), this.leaseMs)) return false
    try {
      await work()
      return true
    } finally {
      await this.leaseStore.releaseLease(this.ownerId)
    }
  }
}
