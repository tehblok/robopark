import type { SyncBatchRequest, SyncBatchResponse } from '../api'
import type { OfflineDb } from './offlineDb'
import type { OfflineAction } from './offlineTypes'
import type { SyncCoordinator } from './syncCoordinator'

export type SyncStatus = 'idle' | 'syncing' | 'offline' | 'attention'
export type SyncState = { status: SyncStatus, pending: number, conflicts: number }
export type OfflineActionInput = Omit<OfflineAction, 'state' | 'attempts' | 'createdAt' | 'updatedAt'>

type EventEnvironment = {
  addEventListener(name: string, listener: EventListener): void
  removeEventListener(name: string, listener: EventListener): void
}

type EngineOptions = {
  db: OfflineDb
  coordinator: SyncCoordinator
  deviceId: string
  sendBatch(batch: SyncBatchRequest, signal?: AbortSignal): Promise<SyncBatchResponse>
  weakLink?: () => boolean
  onRevokedScopes?: (scopes: string[]) => void
  environment?: EventEnvironment
  random?: () => number
  now?: () => number
  scheduleRetry?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>
  cancelRetry?: (timer: ReturnType<typeof setTimeout>) => void
}

function causalOrder(actions: OfflineAction[]): OfflineAction[] {
  const byId = new Map(actions.map(item => [item.id, item]))
  const ordered: OfflineAction[] = []
  const done = new Set<string>()
  const remaining = [...actions].sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id))
  while (remaining.length) {
    const index = remaining.findIndex(item => item.dependencies.every(dep => done.has(dep) || !byId.has(dep)))
    if (index < 0) break
    const [item] = remaining.splice(index, 1)
    ordered.push(item)
    done.add(item.id)
  }
  return ordered
}

export class SyncEngine {
  private readonly db: OfflineDb
  private readonly coordinator: SyncCoordinator
  private readonly deviceId: string
  private readonly sendBatch: EngineOptions['sendBatch']
  private readonly weakLink: () => boolean
  private readonly onRevokedScopes?: (scopes: string[]) => void
  private readonly environment?: EventEnvironment
  private readonly random: () => number
  private readonly now: () => number
  private readonly scheduleRetry: NonNullable<EngineOptions['scheduleRetry']>
  private readonly cancelRetry: NonNullable<EngineOptions['cancelRetry']>
  private readonly subscribers = new Set<() => void>()
  private state: SyncState = { status: 'idle', pending: 0, conflicts: 0 }
  private started = false
  private disposed = false
  private running = false
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private abortController: AbortController | null = null
  private readonly wake = () => { void this.syncNow('event') }

  constructor(options: EngineOptions) {
    this.db = options.db
    this.coordinator = options.coordinator
    this.deviceId = options.deviceId
    this.sendBatch = options.sendBatch
    this.weakLink = options.weakLink ?? (() => false)
    this.onRevokedScopes = options.onRevokedScopes
    this.environment = options.environment ?? (typeof window === 'undefined' ? undefined : window)
    this.random = options.random ?? Math.random
    this.now = options.now ?? Date.now
    this.scheduleRetry = options.scheduleRetry ?? ((callback, delayMs) => setTimeout(callback, delayMs))
    this.cancelRetry = options.cancelRetry ?? (timer => clearTimeout(timer))
  }

  getState(): SyncState { return this.state }
  subscribe(listener: () => void): () => void {
    this.subscribers.add(listener)
    return () => this.subscribers.delete(listener)
  }

  start(): void {
    if (this.started || this.disposed) return
    this.started = true
    this.environment?.addEventListener('online', this.wake)
    this.environment?.addEventListener('focus', this.wake)
    void this.refreshState()
    void this.syncNow('start')
  }

  async enqueueAction(input: OfflineActionInput): Promise<OfflineAction> {
    const now = this.now()
    const action: OfflineAction = { ...input, state: 'ready', attempts: 0, createdAt: now, updatedAt: now }
    if (!await this.db.putAction(action)) throw new Error('offline_scope_inactive')
    await this.refreshState()
    if (this.started) void this.syncNow('enqueue')
    return action
  }

  async cancelAction(id: string): Promise<void> {
    const action = await this.db.getAction(id)
    if (!action || action.state === 'confirmed') return
    await this.db.putAction({ ...action, state: 'cancelled', updatedAt: this.now() })
    await this.refreshState()
  }

  async resolveConflict(id: string, baseRevision: string | null): Promise<void> {
    const action = await this.db.getAction(id)
    if (!action || action.state !== 'conflict') return
    await this.db.putAction({ ...action, state: 'ready', baseRevision, updatedAt: this.now() })
    await this.refreshState()
    void this.syncNow('conflict-resolved')
  }

  async syncNow(_reason: string): Promise<boolean> {
    if (this.disposed || this.running) return false
    let performed = false
    this.running = true
    try {
      await this.coordinator.runExclusive(async () => {
        performed = await this.pump()
      })
      return performed
    } finally {
      this.running = false
    }
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.started = false
    this.environment?.removeEventListener('online', this.wake)
    this.environment?.removeEventListener('focus', this.wake)
    if (this.retryTimer) this.cancelRetry(this.retryTimer)
    this.retryTimer = null
    this.abortController?.abort()
    this.abortController = null
    this.subscribers.clear()
    this.db.close()
  }

  private async pump(): Promise<boolean> {
    const all = await this.db.listActions()
    const ready = causalOrder(all.filter(item => item.state === 'ready' || item.state === 'local'))
    if (!ready.length) { await this.refreshState(); return false }
    const batch = ready.slice(0, this.weakLink() ? 2 : 20)
    const sending = batch.map(item => ({ ...item, state: 'sending' as const, updatedAt: this.now() }))
    for (const item of sending) await this.db.putAction(item)
    this.setState({ ...this.state, status: 'syncing' })
    this.abortController = new AbortController()
    try {
      const response = await this.sendBatch({
        device_id: this.deviceId,
        known_revisions: {},
        actions: sending.map(item => ({
          client_action_id: item.id,
          resource_type: item.resourceType,
          resource_id: item.resourceId,
          action: item.action,
          idempotency_key: item.idempotencyKey,
          base_revision: item.baseRevision,
          park_id: typeof item.payload === 'object' && item.payload && 'park_id' in item.payload ? Number((item.payload as { park_id?: unknown }).park_id) || null : null,
          dependencies: item.dependencies,
          payload: item.payload as Record<string, unknown>,
        })),
      }, this.abortController.signal)
      for (const result of response.results) {
        const item = sending.find(candidate => candidate.id === result.client_action_id)
        if (!item) continue
        const state = result.state === 'confirmed' ? 'confirmed' : result.state === 'conflict' ? 'conflict' : 'attention'
        await this.db.putAction({ ...item, state, updatedAt: this.now() })
      }
      for (const [section, revision] of Object.entries(response.revisions)) {
        await this.db.setRevision(section, String(revision))
      }
      if (response.revoked_scopes.length) this.onRevokedScopes?.(response.revoked_scopes)
      await this.refreshState()
      return true
    } catch {
      let maxAttempts = 1
      for (const item of sending) {
        const attempts = item.attempts + 1
        maxAttempts = Math.max(maxAttempts, attempts)
        await this.db.putAction({ ...item, state: 'ready', attempts, updatedAt: this.now() })
      }
      this.setState({ ...this.state, status: 'offline' })
      const delay = Math.min(60_000, 1_000 * 2 ** (maxAttempts - 1)) * (1 + this.random() * 0.25)
      if (!this.disposed) this.retryTimer = this.scheduleRetry(() => { this.retryTimer = null; void this.syncNow('retry') }, delay)
      return false
    } finally {
      this.abortController = null
    }
  }

  private async refreshState(): Promise<void> {
    const actions = await this.db.listActions()
    const conflicts = actions.filter(item => item.state === 'conflict' || item.state === 'attention').length
    const pending = actions.filter(item => !['confirmed', 'cancelled'].includes(item.state)).length
    this.setState({ status: conflicts ? 'attention' : pending ? this.state.status === 'offline' ? 'offline' : 'idle' : 'idle', pending, conflicts })
  }

  private setState(state: SyncState): void {
    this.state = state
    this.subscribers.forEach(listener => listener())
  }
}
