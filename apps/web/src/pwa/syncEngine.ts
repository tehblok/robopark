import type { SyncBatchRequest, SyncBatchResponse } from '../api'
import type { OfflineDb, SyncTransactionWriter } from './offlineDb'
import type { OfflineAction, OfflineMedia } from './offlineTypes'
import type { LeaseFence, SyncCoordinator } from './syncCoordinator'
import { ClientBatcher } from './clientBatcher'

export type SyncStatus = 'idle' | 'syncing' | 'offline' | 'attention'
export type SyncState = { status: SyncStatus, pending: number, conflicts: number }
export const WEAK_LINK_BATCH_LIMIT = 2
export const SYNC_BATCH_LIMIT = 20
export const MEDIA_CONCURRENCY = 2
class SyncLeaseLostError extends Error {
  constructor() { super('sync_lease_lost') }
}
export type OfflineActionInput = Omit<OfflineAction, 'state' | 'attempts' | 'createdAt' | 'updatedAt' | 'result'>
export type OfflineMediaInput = Omit<OfflineMedia, 'state' | 'attempts' | 'createdAt' | 'updatedAt'>

type EventEnvironment = {
  addEventListener(name: string, listener: EventListener): void
  removeEventListener(name: string, listener: EventListener): void
}

type EngineOptions = {
  db: OfflineDb
  coordinator: SyncCoordinator
  deviceId: string
  sendBatch(batch: SyncBatchRequest, signal?: AbortSignal): Promise<SyncBatchResponse>
  uploadMedia?: (media: OfflineMedia, signal?: AbortSignal) => Promise<void>
  weakLink?: () => boolean
  onRevokedScopes?: (scopes: string[]) => void
  environment?: EventEnvironment
  random?: () => number
  now?: () => number
  scheduleRetry?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>
  cancelRetry?: (timer: ReturnType<typeof setTimeout>) => void
}

function causalOrder(actions: OfflineAction[], knownActions: OfflineAction[], knownMedia: OfflineMedia[]): OfflineAction[] {
  const byId = new Map(actions.map(item => [item.id, item]))
  const actionStates = new Map(knownActions.map(item => [item.id, item.state]))
  const mediaStates = new Map(knownMedia.map(item => [item.id, item.state]))
  const ordered: OfflineAction[] = []
  const done = new Set<string>()
  const remaining = [...actions].sort((a, b) => a.createdAt - b.createdAt || a.id.localeCompare(b.id))
  while (remaining.length) {
    const index = remaining.findIndex(item => item.dependencies.every(dep => {
      if (done.has(dep)) return true
      if (byId.has(dep)) return false
      if (actionStates.has(dep)) return actionStates.get(dep) === 'confirmed'
      if (mediaStates.has(dep)) return mediaStates.get(dep) === 'confirmed'
      return false // The dependency cannot be verified from the local action/media receipt.
    }))
    if (index < 0) break
    const [item] = remaining.splice(index, 1)
    ordered.push(item)
    done.add(item.id)
  }
  return ordered
}

function toBatchAction(item: OfflineAction): SyncBatchRequest['actions'][number] {
  return {
    client_action_id: item.id,
    resource_type: item.resourceType,
    resource_id: item.resourceId,
    action: item.action,
    idempotency_key: item.idempotencyKey,
    base_revision: item.baseRevision,
    park_id: typeof item.payload === 'object' && item.payload && 'park_id' in item.payload ? Number((item.payload as { park_id?: unknown }).park_id) || null : null,
    dependencies: item.dependencies,
    payload: item.payload as Record<string, unknown>,
  }
}

export class SyncEngine {
  private readonly db: OfflineDb
  private readonly coordinator: SyncCoordinator
  private readonly deviceId: string
  private readonly sendBatch: EngineOptions['sendBatch']
  private readonly uploadMedia?: EngineOptions['uploadMedia']
  private readonly weakLink: () => boolean
  private readonly onRevokedScopes?: (scopes: string[]) => void
  private readonly environment?: EventEnvironment
  private readonly random: () => number
  private readonly now: () => number
  private readonly scheduleRetry: NonNullable<EngineOptions['scheduleRetry']>
  private readonly cancelRetry: NonNullable<EngineOptions['cancelRetry']>
  private readonly subscribers = new Set<() => void>()
  private readonly actionSubscribers = new Map<string, Set<(action: OfflineAction | undefined) => void>>()
  private readonly batcher = new ClientBatcher(() => this.syncNow('batch').catch(() => false))
  private networkFallback: NetworkOnlySyncEngine | null = null
  private state: SyncState = { status: 'idle', pending: 0, conflicts: 0 }
  private durableState: SyncState = { status: 'idle', pending: 0, conflicts: 0 }
  private fallbackState: SyncState = { status: 'idle', pending: 0, conflicts: 0 }
  private started = false
  private disposed = false
  private running = false
  private cancelling = false
  private wakeAfterCancellation = false
  private activeRun: Promise<unknown> | null = null
  private wakeAfterCurrent = false
  private manualAttentionRetryRequested = false
  private retryTimer: ReturnType<typeof setTimeout> | null = null
  private retryGeneration = 0
  private abortController: AbortController | null = null
  private lastActionCreatedAt = Number.NEGATIVE_INFINITY
  private readonly wake = () => { void this.syncNow('event').catch(() => {}) }

  constructor(options: EngineOptions) {
    this.db = options.db
    this.coordinator = options.coordinator
    this.deviceId = options.deviceId
    this.sendBatch = options.sendBatch
    this.uploadMedia = options.uploadMedia
    this.weakLink = options.weakLink ?? (() => false)
    this.onRevokedScopes = options.onRevokedScopes
    this.environment = options.environment ?? (typeof window === 'undefined' ? undefined : window)
    this.random = options.random ?? Math.random
    this.now = options.now ?? Date.now
    this.scheduleRetry = options.scheduleRetry ?? ((callback, delayMs) => setTimeout(callback, delayMs))
    this.cancelRetry = options.cancelRetry ?? (timer => clearTimeout(timer))
  }

  getState(): SyncState { return this.state }
  getProjection(resourceType: string, resourceId: string): unknown {
    return this.batcher.getProjection(`${resourceType}:${resourceId}`)
  }
  subscribeProjection(listener: () => void): () => void { return this.batcher.subscribe(listener) }
  subscribe(listener: () => void): () => void {
    this.subscribers.add(listener)
    return () => this.subscribers.delete(listener)
  }

  subscribeAction(id: string, listener: (action: OfflineAction | undefined) => void): () => void {
    const listeners = this.actionSubscribers.get(id) ?? new Set()
    let active = true
    let notified = false
    const deliver = (action: OfflineAction | undefined) => {
      if (!active) return
      notified = true
      listener(action)
    }
    listeners.add(deliver)
    this.actionSubscribers.set(id, listeners)
    void this.db.getAction(id).then(action => {
      if (active && !notified) listener(action)
    }).catch(() => {
      if (active && !notified) listener(undefined)
    })
    return () => {
      active = false
      listeners.delete(deliver)
      if (!listeners.size) this.actionSubscribers.delete(id)
    }
  }

  async findAction(resourceId: string, action: string): Promise<OfflineAction | undefined> {
    return (await this.db.listActions())
      .filter(item => item.resourceId === resourceId && item.action === action
        && !['confirmed', 'cancelled'].includes(item.state))
      .sort((left, right) => right.updatedAt - left.updatedAt)[0]
  }

  async listActions(): Promise<OfflineAction[]> {
    return this.db.listActions()
  }

  async listMedia(): Promise<OfflineMedia[]> {
    return this.db.listMedia()
  }

  start(): void {
    if (this.started || this.disposed) return
    this.started = true
    this.environment?.addEventListener('online', this.wake)
    this.environment?.addEventListener('focus', this.wake)
    void this.refreshState().catch(() => {})
    void this.syncNow('start').catch(() => {})
  }

  async enqueueAction(input: OfflineActionInput): Promise<OfflineAction> {
    return this.enqueueOptimistic(input)
  }

  enqueueOptimistic(input: OfflineActionInput, projection?: unknown): Promise<OfflineAction> {
    const now = Math.max(this.now(), this.lastActionCreatedAt + 1)
    this.lastActionCreatedAt = now
    const action: OfflineAction = { ...input, state: 'ready', attempts: 0, createdAt: now, updatedAt: now }
    if (projection !== undefined) this.batcher.project(action.id, `${action.resourceType}:${action.resourceId}`, projection)
    return this.persistOrSend(action)
  }

  private async persistOrSend(action: OfflineAction): Promise<OfflineAction> {
    try {
      const stored = await this.db.putQueuedAction(action)
      if (!stored) throw new Error('offline_scope_inactive')
      action = stored
      this.lastActionCreatedAt = Math.max(this.lastActionCreatedAt, action.createdAt)
      if (['confirmed', 'cancelled', 'conflict', 'attention'].includes(action.state)) this.batcher.settle(action.id)
    } catch (error) {
      if (!this.db.isGenerationCurrent() || (error instanceof Error
        && ['offline_scope_inactive', 'sync_payload_conflict'].includes(error.message))) {
        this.batcher.settle(action.id)
        throw error
      }
      // Denied/quota-limited writes share the same 150 ms transport batch and
      // are accepted only after the server confirms them.
      if (!this.networkFallback) {
        this.networkFallback = new NetworkOnlySyncEngine({ deviceId: this.deviceId, sendBatch: this.sendBatch, onRevokedScopes: this.onRevokedScopes })
        this.networkFallback.subscribe(() => {
          const fallback = this.networkFallback?.getState()
          if (fallback) {
            this.fallbackState = fallback
            this.publishCombinedState()
          }
        })
      }
      // A quota failure does not imply that earlier durable actions/media are
      // gone. Count them before the transient network batch is accepted.
      await this.refreshState().catch(() => {})
      const { state: _state, attempts: _attempts, createdAt: _createdAt, updatedAt: _updatedAt, result: _result, ...input } = action
      const stop = this.networkFallback.subscribeAction(action.id, next => {
        this.actionSubscribers.get(action.id)?.forEach(listener => listener(next))
      })
      try {
        const confirmed = await this.networkFallback.enqueueAction(input)
        this.batcher.settle(action.id)
        return confirmed
      } catch (reason) {
        this.batcher.settle(action.id)
        throw reason
      } finally {
        stop()
      }
    }
    await this.refreshState()
    if (this.started) this.batcher.schedule()
    return action
  }

  async enqueueMedia(input: OfflineMediaInput, dependentAction?: OfflineActionInput): Promise<OfflineMedia> {
    const now = this.now()
    const media: OfflineMedia = { ...input, state: 'ready', attempts: 0, createdAt: now, updatedAt: now }
    let savedMedia = media
    let needsSync = true
    if (dependentAction && dependentAction.id !== input.actionId) throw new Error('media_action_identity_mismatch')
    if (dependentAction) {
      const actionTime = Math.max(now, this.lastActionCreatedAt + 1)
      this.lastActionCreatedAt = actionTime
      const action: OfflineAction = { ...dependentAction, state: 'ready', attempts: 0, createdAt: actionTime, updatedAt: now }
      const stored = await this.db.putQueuedAction(action, media)
      if (!stored) throw new Error('offline_scope_inactive')
      if (stored.state === 'cancelled') throw new Error('sync_action_already_terminal')
      savedMedia = await this.db.getMedia(media.id) ?? (stored.state === 'confirmed'
        ? { ...media, state: 'confirmed' } : media)
      needsSync = stored.state === 'ready' || stored.state === 'local' || stored.state === 'sending'
    } else if (!await this.db.putMedia(media)) throw new Error('offline_scope_inactive')
    await this.refreshState()
    if (this.started && needsSync) void this.syncNow('media').catch(() => {})
    return savedMedia
  }

  async cancelAction(id: string): Promise<void> {
    if (this.running || this.cancelling) throw new Error('sync_busy')
    this.cancelling = true
    let committed = false
    try {
      const performed = await this.coordinator.runExclusive(async (_leaseSignal, ensureLease, fence) => {
        const action = await this.db.getAction(id)
        if (!action || ['confirmed', 'cancelled'].includes(action.state)) return
        if (action.state === 'sending') throw new Error('sync_busy')
        const media = (await this.db.listMedia()).filter(item => item.actionId === id && item.state !== 'confirmed')
        if (media.some(item => item.state === 'uploading')) throw new Error('sync_busy')
        const otherActions = (await this.db.listActions()).filter(item => item.id !== id && !['confirmed', 'cancelled'].includes(item.state))
        if (otherActions.some(other => other.dependencies.includes(id)
          || media.some(item => other.dependencies.includes(item.id)))) throw new Error('sync_dependency_in_use')
        if (!await ensureLease()) throw new Error('sync_busy')
        await this.writeUnderLease(fence, writer => {
          writer.putAction({ ...action, state: 'cancelled', updatedAt: this.now() })
          for (const item of media) writer.deleteMedia(item.id)
        })
        committed = true
        this.batcher.settle(id)
      })
      if (!performed && !committed) throw new Error('sync_busy')
      await this.refreshState()
    } catch (error) {
      if (error instanceof SyncLeaseLostError) throw new Error('sync_busy')
      throw error
    } finally {
      this.cancelling = false
      if (this.wakeAfterCancellation && !this.disposed) {
        this.wakeAfterCancellation = false
        this.batcher.schedule()
      }
    }
  }

  async resolveConflict(id: string, baseRevision: string | null): Promise<void> {
    const action = await this.db.getAction(id)
    if (!action || action.state !== 'conflict') return
    await this.db.putAction({ ...action, state: 'ready', baseRevision, updatedAt: this.now() })
    await this.refreshState()
    void this.syncNow('conflict-resolved').catch(() => {})
  }

  async syncNow(reason: string): Promise<boolean> {
    if (this.disposed) return false
    if (reason === 'manual') this.manualAttentionRetryRequested = true
    if (this.cancelling) { this.wakeAfterCancellation = true; return false }
    if (this.running) { this.wakeAfterCurrent = true; return false }
    this.cancelScheduledRetry()
    this.batcher.cancelScheduled()
    let performed = false
    let consumedManualAttentionRetry = false
    this.running = true
    try {
      const work = this.coordinator.runExclusive(async (leaseSignal, ensureLease, fence) => {
        const retryAttentionMedia = this.manualAttentionRetryRequested
        consumedManualAttentionRetry = retryAttentionMedia
        this.manualAttentionRetryRequested = false
        performed = await this.pump(retryAttentionMedia, leaseSignal, ensureLease, fence)
      })
      this.activeRun = work
      const acquired = await work
      if (!acquired && consumedManualAttentionRetry && !this.disposed) this.manualAttentionRetryRequested = true
      if (!acquired && !this.disposed && await this.hasWorkAwaitingLease(this.manualAttentionRetryRequested) && !this.disposed) {
        this.scheduleSyncRetry(this.manualAttentionRetryRequested ? 'manual' : 'lease-retry', 1_000)
      }
      return acquired && performed
    } catch (error) {
      if (!(error instanceof SyncLeaseLostError)) throw error
      if (!this.disposed) {
        if (consumedManualAttentionRetry) this.manualAttentionRetryRequested = true
        await this.refreshState()
        this.setState({ ...this.durableState, status: 'offline' })
        this.scheduleSyncRetry(this.manualAttentionRetryRequested ? 'manual' : 'lease-retry', 1_000)
      }
      return false
    } finally {
      this.activeRun = null
      this.running = false
      if (this.disposed) this.db.close()
      if (this.wakeAfterCurrent && !this.disposed) {
        this.wakeAfterCurrent = false
        this.batcher.schedule()
      }
    }
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.started = false
    this.manualAttentionRetryRequested = false
    this.environment?.removeEventListener('online', this.wake)
    this.environment?.removeEventListener('focus', this.wake)
    this.cancelScheduledRetry()
    this.abortController?.abort()
    this.abortController = null
    this.subscribers.clear()
    this.actionSubscribers.clear()
    this.batcher.dispose()
    this.networkFallback?.dispose()
    if (!this.activeRun) this.db.close()
  }

  private cancelScheduledRetry(): void {
    this.retryGeneration += 1
    if (this.retryTimer !== null) this.cancelRetry(this.retryTimer)
    this.retryTimer = null
  }

  private scheduleSyncRetry(reason: string, delay: number): void {
    if (this.disposed) return
    this.cancelScheduledRetry()
    const generation = this.retryGeneration
    this.retryTimer = this.scheduleRetry(() => {
      if (this.disposed || generation !== this.retryGeneration) return
      this.retryTimer = null
      void this.syncNow(reason).catch(() => {})
    }, delay)
  }

  private async hasWorkAwaitingLease(retryAttentionMedia: boolean): Promise<boolean> {
    const [actions, media] = await Promise.all([this.db.listActions(), this.db.listMedia()])
    return actions.some(item => ['local', 'ready', 'sending'].includes(item.state))
      || media.some(item => ['local', 'ready', 'uploading'].includes(item.state)
        || (retryAttentionMedia && item.state === 'attention'))
  }

  private async writeUnderLease(fence: LeaseFence, mutator: (writer: SyncTransactionWriter) => unknown): Promise<void> {
    if (fence) {
      if (!await this.db.transactionIfLease(fence.owner, fence.now, mutator)) throw new SyncLeaseLostError()
    } else await this.db.transaction(mutator)
  }

  private async pump(retryAttentionMedia: boolean, leaseSignal: AbortSignal, ensureLease: () => Promise<boolean>, fence: LeaseFence): Promise<boolean> {
    await this.recoverInterruptedTransfers(fence)
    const pendingMedia = (await this.db.listMedia()).filter(item => item.state === 'local' || item.state === 'ready' || (retryAttentionMedia && item.state === 'attention'))
    if (pendingMedia.length && this.uploadMedia) {
      this.setState({ ...this.durableState, status: 'syncing' })
      this.abortController = new AbortController()
      const mediaController = this.abortController
      const abortMedia = () => mediaController.abort('sync_lease_lost')
      leaseSignal.addEventListener('abort', abortMedia, { once: true })
      try {
        for (const media of pendingMedia.slice(0, this.weakLink() ? 1 : MEDIA_CONCURRENCY)) {
          if (!await ensureLease()) throw new Error('sync_lease_lost')
          await this.writeUnderLease(fence, writer => writer.putMedia({ ...media, state: 'uploading', updatedAt: this.now() }))
          await this.uploadMedia(media, mediaController.signal)
          if (!await ensureLease()) throw new Error('sync_lease_lost')
          await this.writeUnderLease(fence, writer => writer.putMedia({ ...media, originalBlob: undefined, state: 'confirmed', updatedAt: this.now() }))
        }
      } catch (error) {
        if (leaseSignal.aborted || error instanceof SyncLeaseLostError) {
          await this.refreshState()
          this.setState({ ...this.durableState, status: 'offline' })
          this.scheduleSyncRetry('lease-retry', 1_000)
          return false
        }
        let maxAttempts = 1
        const status = typeof error === 'object' && error !== null && 'status' in error ? Number((error as { status?: unknown }).status) : 0
        const permanent = status >= 400 && status < 500 && ![408, 425, 429].includes(status)
        for (const media of pendingMedia) {
          if ((await this.db.getMedia(media.id))?.state === 'uploading') {
            const attempts = (media.attempts ?? 0) + 1
            maxAttempts = Math.max(maxAttempts, attempts)
            await this.writeUnderLease(fence, writer => writer.putMedia({ ...media, state: permanent ? 'attention' : 'ready', attempts, updatedAt: this.now() }))
          }
        }
        await this.refreshState()
        if (!permanent) {
          this.setState({ ...this.durableState, status: 'offline' })
          const delay = Math.min(60_000, 1_000 * 2 ** (maxAttempts - 1)) * (1 + this.random() * 0.25)
          this.scheduleSyncRetry('media-retry', delay)
        }
        return false
      } finally {
        leaseSignal.removeEventListener('abort', abortMedia)
        this.abortController = null
      }
    }
    const all = await this.db.listActions()
    const allMedia = await this.db.listMedia()
    const actionStates = new Map(all.map(item => [item.id, item.state]))
    const mediaIds = new Set(allMedia.map(item => item.id))
    const broken = all.filter(item => (item.state === 'ready' || item.state === 'local')
      && item.dependencies.some(dep => actionStates.get(dep) === 'cancelled'
        || (!actionStates.has(dep) && !mediaIds.has(dep))))
    if (broken.length) {
      const now = this.now()
      await this.writeUnderLease(fence, writer => {
        for (const item of broken) writer.putAction({ ...item, state: 'attention',
          result: { code: 'offline_dependency_missing' }, updatedAt: now })
      })
      for (const item of broken) this.batcher.settle(item.id)
    }
    const brokenIds = new Set(broken.map(item => item.id))
    const currentActions = all.map(item => brokenIds.has(item.id) ? { ...item, state: 'attention' as const } : item)
    const unconfirmedMediaActions = new Set(allMedia
      .filter(item => item.state !== 'confirmed').map(item => item.actionId))
    const ready = causalOrder(currentActions.filter(item => (item.state === 'ready' || item.state === 'local') && !unconfirmedMediaActions.has(item.id)), currentActions, allMedia)
    if (!ready.length) { await this.refreshState(); return false }
    const batch = ready.slice(0, this.weakLink() ? WEAK_LINK_BATCH_LIMIT : SYNC_BATCH_LIMIT)
    const sending = batch.map(item => ({ ...item, state: 'sending' as const, updatedAt: this.now() }))
    for (const item of sending) await this.writeUnderLease(fence, writer => writer.putAction(item))
    this.setState({ ...this.durableState, status: 'syncing' })
    this.abortController = new AbortController()
    const batchController = this.abortController
    const abortBatch = () => batchController.abort('sync_lease_lost')
    leaseSignal.addEventListener('abort', abortBatch, { once: true })
    try {
      if (!await ensureLease()) throw new Error('sync_lease_lost')
      const response = await this.sendBatch({
        device_id: this.deviceId,
        known_revisions: {},
        actions: sending.map(toBatchAction),
      }, batchController.signal)
      if (!await ensureLease()) throw new Error('sync_lease_lost')
      let retryAttempts = 0
      for (const result of response.results) {
        const item = sending.find(candidate => candidate.id === result.client_action_id)
        if (!item) continue
        const dependencyStillPendingLocally = item.dependencies.some(dep =>
          actionStates.has(dep) && actionStates.get(dep) !== 'confirmed')
        if (result.state === 'attention' && (result.code === 'dependency_missing'
          || (result.code === 'dependency_failed' && !dependencyStillPendingLocally))) {
          await this.writeUnderLease(fence, writer => writer.putAction({ ...item,
            state: 'attention', result: { code: result.code }, updatedAt: this.now() }))
          this.batcher.settle(item.id)
        } else if (result.state === 'attention') {
          const attempts = (item.attempts ?? 0) + 1
          retryAttempts = Math.max(retryAttempts, attempts)
          await this.writeUnderLease(fence, writer => writer.putAction({ ...item, state: 'ready', attempts, updatedAt: this.now() }))
        } else if (result.state === 'conflict' && ['media_upload_missing', 'media_dependency_pending'].includes(result.code ?? '')) {
          const mediaId = item.action === 'submit_review' && typeof item.payload === 'object' && item.payload
            ? String((item.payload as { media_id?: unknown }).media_id ?? '')
            : ''
          const media = mediaId ? await this.db.getMedia(mediaId) : undefined
          if (media && media.actionId === item.id && media.sizeBytes > 0) {
            const attempts = (item.attempts ?? 0) + 1
            retryAttempts = Math.max(retryAttempts, attempts)
            await this.writeUnderLease(fence, writer => {
              writer.putMedia({ ...media, state: 'ready', attempts: (media.attempts ?? 0) + 1, updatedAt: this.now() })
              writer.putAction({ ...item, state: 'ready', attempts, updatedAt: this.now() })
            })
          } else {
            await this.writeUnderLease(fence, writer => writer.putAction({ ...item, state: 'conflict', updatedAt: this.now() }))
            this.batcher.settle(item.id)
          }
        } else {
          const state = result.state === 'confirmed' ? 'confirmed' : result.state === 'conflict' ? 'conflict' : 'attention'
          const outcome = result.state === 'confirmed' || !result.code
            ? result.result : { ...result.result, code: result.code }
          await this.writeUnderLease(fence, writer => writer.putAction({ ...item, state, result: outcome, updatedAt: this.now() }))
          if (state === 'confirmed' || state === 'conflict') this.batcher.settle(item.id)
        }
      }
      const answered = new Set(response.results.map(result => result.client_action_id))
      for (const item of sending) {
        if (answered.has(item.id)) continue
        const attempts = (item.attempts ?? 0) + 1
        retryAttempts = Math.max(retryAttempts, attempts)
        await this.writeUnderLease(fence, writer => writer.putAction({ ...item, state: 'ready', attempts, updatedAt: this.now() }))
      }
      if (Object.keys(response.revisions).length) await this.writeUnderLease(fence, writer => {
        for (const [section, revision] of Object.entries(response.revisions)) writer.putRevision(section, String(revision))
      })
      await this.refreshState()
      if (response.revoked_scopes.length) this.onRevokedScopes?.(response.revoked_scopes)
      if (retryAttempts && !this.disposed) {
        const delay = Math.min(60_000, 1_000 * 2 ** (retryAttempts - 1)) * (1 + this.random() * 0.25)
        this.scheduleSyncRetry('server-retry', delay)
      }
      return true
    } catch (error) {
      if (leaseSignal.aborted || error instanceof SyncLeaseLostError) {
        await this.refreshState()
        this.setState({ ...this.durableState, status: 'offline' })
        this.scheduleSyncRetry('lease-retry', 1_000)
        return false
      }
      let maxAttempts = 1
      for (const item of sending) {
        const attempts = (item.attempts ?? 0) + 1
        maxAttempts = Math.max(maxAttempts, attempts)
        await this.writeUnderLease(fence, writer => writer.putAction({ ...item, state: 'ready', attempts, updatedAt: this.now() }))
      }
      this.setState({ ...this.durableState, status: 'offline' })
      const delay = Math.min(60_000, 1_000 * 2 ** (maxAttempts - 1)) * (1 + this.random() * 0.25)
      this.scheduleSyncRetry('retry', delay)
      return false
    } finally {
      leaseSignal.removeEventListener('abort', abortBatch)
      this.abortController = null
    }
  }

  private async recoverInterruptedTransfers(fence: LeaseFence): Promise<void> {
    const interruptedActions = (await this.db.listActions()).filter(item => item.state === 'sending')
    const interruptedMedia = (await this.db.listMedia()).filter(item => item.state === 'uploading')
    if (!interruptedActions.length && !interruptedMedia.length) return
    const now = this.now()
    await this.writeUnderLease(fence, writer => {
      for (const action of interruptedActions) {
        writer.putAction({ ...action, state: 'ready', attempts: (action.attempts ?? 0) + 1, updatedAt: now })
      }
      for (const media of interruptedMedia) {
        writer.putMedia({ ...media, state: 'ready', attempts: (media.attempts ?? 0) + 1, updatedAt: now })
      }
    })
  }

  private async refreshState(): Promise<void> {
    const actions = await this.db.listActions()
    const media = await this.db.listMedia()
    const conflicts = actions.filter(item => item.state === 'conflict' || item.state === 'attention').length
      + media.filter(item => item.state === 'attention').length
    const pending = actions.filter(item => !['confirmed', 'cancelled'].includes(item.state)).length
      + media.filter(item => item.state !== 'confirmed').length
    this.setState({ status: conflicts ? 'attention' : pending && this.durableState.status === 'offline' ? 'offline' : 'idle', pending, conflicts })
    for (const [id, listeners] of this.actionSubscribers) {
      const action = actions.find(item => item.id === id)
      listeners.forEach(listener => listener(action))
    }
  }

  private setState(state: SyncState): void {
    this.durableState = state
    this.publishCombinedState()
  }

  private publishCombinedState(): void {
    const durable = this.durableState
    const fallback = this.fallbackState
    const conflicts = durable.conflicts + fallback.conflicts
    const status: SyncStatus = conflicts || durable.status === 'attention' || fallback.status === 'attention' ? 'attention'
      : durable.status === 'syncing' || fallback.status === 'syncing' ? 'syncing'
        : durable.status === 'offline' || fallback.status === 'offline' ? 'offline' : 'idle'
    this.state = { status, pending: durable.pending + fallback.pending, conflicts }
    this.subscribers.forEach(listener => listener())
  }
}

type NetworkPending = {
  action: OfflineAction
  signature: string
  promise: Promise<OfflineAction>
  resolve: (action: OfflineAction) => void
  reject: (reason: unknown) => void
}

/** Direct transport used only when durable browser storage cannot be opened. */
export class NetworkOnlySyncEngine {
  private readonly options: Pick<EngineOptions, 'deviceId' | 'sendBatch' | 'uploadMedia' | 'onRevokedScopes'>
  private readonly pending = new Map<string, NetworkPending>()
  private readonly listeners = new Set<() => void>()
  private readonly actionSubscribers = new Map<string, Set<(action: OfflineAction | undefined) => void>>()
  private readonly batcher = new ClientBatcher(() => this.syncNow('batch'))
  private state: SyncState = { status: 'idle', pending: 0, conflicts: 0 }
  private disposed = false
  private sending = false
  private readonly mediaAbortControllers = new Set<AbortController>()

  constructor(options: Pick<EngineOptions, 'deviceId' | 'sendBatch' | 'uploadMedia' | 'onRevokedScopes'>) { this.options = options }

  start(): void {}
  getState(): SyncState { return this.state }
  subscribe(listener: () => void): () => void { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  subscribeProjection(listener: () => void): () => void { return this.batcher.subscribe(listener) }
  getProjection(resourceType: string, resourceId: string): unknown { return this.batcher.getProjection(`${resourceType}:${resourceId}`) }

  enqueueAction(input: OfflineActionInput): Promise<OfflineAction> { return this.enqueueOptimistic(input) }
  enqueueOptimistic(input: OfflineActionInput, projection?: unknown): Promise<OfflineAction> {
    if (this.disposed) return Promise.reject(new Error('sync_disposed'))
    const now = Date.now()
    const action: OfflineAction = { ...input, state: 'ready', attempts: 0, createdAt: now, updatedAt: now }
    const signature = JSON.stringify(toBatchAction(action))
    const existing = this.pending.get(action.id)
    if (existing) return existing.signature === signature
      ? existing.promise : Promise.reject(new Error('sync_payload_conflict'))
    if (projection !== undefined) this.batcher.project(action.id, `${action.resourceType}:${action.resourceId}`, projection)
    let resolve!: (value: OfflineAction) => void
    let reject!: (reason: unknown) => void
    const promise = new Promise<OfflineAction>((onResolve, onReject) => { resolve = onResolve; reject = onReject })
    this.pending.set(action.id, { action, signature, promise, resolve, reject })
    this.notifyAction(action.id, action)
    this.publish({ status: 'idle', pending: this.pending.size, conflicts: 0 })
    this.batcher.schedule()
    return promise
  }

  async enqueueMedia(input: OfflineMediaInput, dependentAction?: OfflineActionInput): Promise<OfflineMedia> {
    if (this.disposed) throw new Error('sync_disposed')
    if (!this.options.uploadMedia) throw new Error('media_network_unavailable')
    if (dependentAction && dependentAction.id !== input.actionId) throw new Error('media_action_identity_mismatch')
    const now = Date.now()
    const media: OfflineMedia = { ...input, state: 'uploading', attempts: 0, createdAt: now, updatedAt: now }
    const controller = new AbortController()
    this.mediaAbortControllers.add(controller)
    this.publish({ status: 'syncing', pending: 1, conflicts: 0 })
    try {
      await this.options.uploadMedia(media, controller.signal)
      if (controller.signal.aborted || this.disposed) throw new Error('sync_disposed')
      if (dependentAction) await this.enqueueAction(dependentAction)
      this.publish({ status: 'idle', pending: 0, conflicts: 0 })
      return { ...media, state: 'confirmed' }
    } catch (error) {
      this.publish({ status: 'attention', pending: 0, conflicts: 1 })
      throw error
    } finally {
      this.mediaAbortControllers.delete(controller)
    }
  }

  async syncNow(_reason: string): Promise<boolean> {
    if (this.disposed || this.sending || this.pending.size === 0) return false
    this.batcher.cancelScheduled()
    this.sending = true
    const items = [...this.pending.values()].slice(0, SYNC_BATCH_LIMIT)
    for (const item of items) this.notifyAction(item.action.id, { ...item.action, state: 'sending' })
    this.publish({ status: 'syncing', pending: this.pending.size, conflicts: 0 })
    try {
      const response = await this.options.sendBatch({
        device_id: this.options.deviceId, known_revisions: {}, actions: items.map(item => toBatchAction(item.action)),
      })
      for (const item of items) {
        const result = response.results.find(candidate => candidate.client_action_id === item.action.id)
        this.pending.delete(item.action.id)
        this.batcher.settle(item.action.id)
        if (result?.state === 'confirmed') {
          const confirmed = { ...item.action, state: 'confirmed' as const, result: result.result }
          this.notifyAction(item.action.id, confirmed)
          item.resolve(confirmed)
        } else {
          this.notifyAction(item.action.id, { ...item.action, state: result?.state === 'conflict' ? 'conflict' : 'attention' })
          item.reject(new Error(result?.state === 'conflict' ? 'sync_conflict' : 'sync_not_confirmed'))
        }
      }
      const conflicts = items.filter(item => response.results.find(result => result.client_action_id === item.action.id)?.state !== 'confirmed').length
      this.publish({ status: conflicts ? 'attention' : 'idle', pending: this.pending.size, conflicts })
      if (response.revoked_scopes.length) this.options.onRevokedScopes?.(response.revoked_scopes)
      return true
    } catch (error) {
      for (const item of items) {
        this.pending.delete(item.action.id)
        this.batcher.settle(item.action.id)
        this.notifyAction(item.action.id, { ...item.action, state: 'attention' })
        item.reject(error)
      }
      this.publish({ status: 'attention', pending: this.pending.size, conflicts: items.length })
      return false
    } finally {
      this.sending = false
      if (this.pending.size) this.batcher.schedule()
    }
  }

  async cancelAction(id: string): Promise<void> {
    const item = this.pending.get(id)
    if (!item) return
    this.pending.delete(id)
    this.batcher.settle(id)
    this.notifyAction(id, { ...item.action, state: 'cancelled' })
    item.reject(new Error('sync_cancelled'))
    this.publish({ status: 'idle', pending: this.pending.size, conflicts: 0 })
  }
  async resolveConflict(): Promise<void> {}
  async findAction(resourceId: string, action: string): Promise<OfflineAction | undefined> {
    return [...this.pending.values()].find(item => item.action.resourceId === resourceId && item.action.action === action)?.action
  }
  async listActions(): Promise<OfflineAction[]> {
    return [...this.pending.values()].map(item => item.action)
  }
  async listMedia(): Promise<OfflineMedia[]> { return [] }
  subscribeAction(id: string, listener: (action: OfflineAction | undefined) => void): () => void {
    const listeners = this.actionSubscribers.get(id) ?? new Set()
    listeners.add(listener)
    this.actionSubscribers.set(id, listeners)
    listener(this.pending.get(id)?.action)
    return () => { listeners.delete(listener); if (!listeners.size) this.actionSubscribers.delete(id) }
  }
  dispose(): void {
    this.disposed = true
    for (const controller of this.mediaAbortControllers) controller.abort(new Error('session_changed'))
    this.mediaAbortControllers.clear()
    for (const item of this.pending.values()) item.reject(new Error('sync_disposed'))
    this.pending.clear()
    this.batcher.dispose()
    this.listeners.clear()
    this.actionSubscribers.clear()
  }
  private notifyAction(id: string, action: OfflineAction | undefined): void {
    this.actionSubscribers.get(id)?.forEach(listener => listener(action))
  }
  private publish(state: SyncState): void { this.state = state; for (const listener of this.listeners) listener() }
}
