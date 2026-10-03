export type LeaseStore = {
  claimLease(owner: string, now: number, leaseMs: number): Promise<boolean>
  renewLease(owner: string, now: number, leaseMs: number): Promise<boolean>
  releaseLease(owner: string): Promise<void>
}

export type LeaseFence = { owner: string, now: () => number } | null

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

  async runExclusive(work: (signal: AbortSignal, ensureLease: () => Promise<boolean>, fence: LeaseFence) => Promise<void> | void): Promise<boolean> {
    if (this.lockManager) {
      return this.lockManager.request('robopark-sync', { ifAvailable: true }, async lock => {
        if (!lock) return false
        await work(new AbortController().signal, async () => true, null)
        return true
      })
    }
    if (!await this.leaseStore.claimLease(this.ownerId, this.now(), this.leaseMs)) return false
    const controller = new AbortController()
    let renewal = Promise.resolve()
    const ensureLease = async () => {
      renewal = renewal.then(async () => {
        if (controller.signal.aborted) return
        if (!await this.leaseStore.renewLease(this.ownerId, this.now(), this.leaseMs)) {
          controller.abort('sync_lease_lost')
        }
      }).catch(() => controller.abort('sync_lease_lost'))
      await renewal
      return !controller.signal.aborted
    }
    const heartbeat = setInterval(() => { void ensureLease() }, Math.max(1, Math.floor(this.leaseMs / 3)))
    try {
      await work(controller.signal, ensureLease, { owner: this.ownerId, now: this.now })
      return await ensureLease()
    } finally {
      clearInterval(heartbeat)
      await renewal
      await this.leaseStore.releaseLease(this.ownerId)
    }
  }
}
