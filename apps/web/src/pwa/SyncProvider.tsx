import { createContext, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from 'react'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { useParkScope } from '../app/park/parkScope'
import { offlineScopeForUser } from '../lib/deviceResourceCache'
import { estimateOfflineBudget, OfflineStorageFullError, openOfflineDb } from './offlineDb'
import { storageRegistry } from './storageRegistry'
import { SyncCoordinator } from './syncCoordinator'
import { NetworkOnlySyncEngine, SyncEngine, type OfflineActionInput, type OfflineMediaInput, type SyncState } from './syncEngine'
import type { OfflineAction, OfflineMedia } from './offlineTypes'
import { uploadMedia } from './resumableUpload'
import { ClientTelemetry } from './clientTelemetry'
import { setServiceWorkerSyncState } from './registerServiceWorker'

export type SyncEngineLike = {
  start(): void
  dispose(): void
  subscribe(listener: () => void): () => void
  getState(): SyncState
  enqueueAction?(input: OfflineActionInput): Promise<unknown>
  enqueueOptimistic?(input: OfflineActionInput, projection?: unknown): Promise<unknown>
  enqueueMedia?(input: OfflineMediaInput): Promise<unknown>
  syncNow?(reason: string): Promise<boolean>
  cancelAction?(id: string): Promise<void>
  resolveConflict?(id: string, baseRevision: string | null): Promise<void>
  findAction?(resourceId: string, action: string): Promise<OfflineAction | undefined>
  subscribeAction?(id: string, listener: (action: OfflineAction | undefined) => void): () => void
  getProjection?(resourceType: string, resourceId: string): unknown
  subscribeProjection?(listener: () => void): () => void
}
export type SyncEngineFactory = (options: { accountId: number, park: string, user: ReturnType<typeof useAuth>['user'] }) => Promise<SyncEngineLike>

export type SyncContextValue = {
  state: SyncState
  actionTrackingReady?: boolean
  enqueueAction(input: OfflineActionInput): Promise<unknown>
  enqueueOptimistic?(input: OfflineActionInput, projection?: unknown): Promise<unknown>
  enqueueMedia(input: OfflineMediaInput): Promise<unknown>
  syncNow(reason?: string): Promise<boolean>
  cancelAction(id: string): Promise<void>
  resolveConflict(id: string, baseRevision: string | null): Promise<void>
  findAction(resourceId: string, action: string): Promise<OfflineAction | undefined>
  subscribeAction(id: string, listener: (action: OfflineAction | undefined) => void): () => void
  getProjection?(resourceType: string, resourceId: string): unknown
  subscribeProjection?(listener: () => void): () => void
}

const DEFAULT_STATE: SyncState = { status: 'idle', pending: 0, conflicts: 0 }
const SyncContext = createContext<SyncContextValue | null>(null)

export function SyncContextProvider({ children, value }: PropsWithChildren<{ value: SyncContextValue }>) {
  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>
}

async function defaultEngineFactory(options: Parameters<SyncEngineFactory>[0]): Promise<SyncEngineLike> {
  if (!options.user) throw new Error('sync_user_required')
  const scope = offlineScopeForUser(options.user, options.park)
  const sendBatch = (batch: Parameters<typeof api.syncBatch>[0], signal?: AbortSignal) => api.syncBatch(batch, signal)
  const sendMedia = (media: OfflineMedia) => uploadMedia(
    { id: media.id, blob: media.blob, mimeType: media.mimeType, sha256: media.sha256, name: media.name },
    {
      create: input => api.createMediaUpload({ ...input, issue_key: media.issueKey }),
      putChunk: (uploadId, offset, chunk, sha256) => api.putMediaChunk(uploadId, offset, chunk, sha256),
      complete: uploadId => api.completeMediaUpload(uploadId),
    },
  ).then(() => undefined)
  let db: Awaited<ReturnType<typeof openOfflineDb>>
  try {
    db = await openOfflineDb(scope)
  } catch {
    return new NetworkOnlySyncEngine({ deviceId: `account-${options.accountId}`, sendBatch, uploadMedia: sendMedia })
  }
  try {
    await db.cleanup({ maxBytes: await estimateOfflineBudget() })
  } catch (error) {
    if (!(error instanceof OfflineStorageFullError)) {
      db.close()
      return new NetworkOnlySyncEngine({ deviceId: `account-${options.accountId}`, sendBatch, uploadMedia: sendMedia })
    }
  }
  const lockManager = typeof navigator !== 'undefined' && 'locks' in navigator
    ? navigator.locks as unknown as ConstructorParameters<typeof SyncCoordinator>[0]['lockManager']
    : undefined
  const coordinator = new SyncCoordinator({
    ownerId: globalThis.crypto.randomUUID(),
    leaseStore: db,
    lockManager,
  })
  return new SyncEngine({
    db,
    coordinator,
    deviceId: `account-${options.accountId}`,
    sendBatch,
    uploadMedia: sendMedia,
    weakLink: () => {
      const connection = (navigator as Navigator & { connection?: { effectiveType?: string, saveData?: boolean } }).connection
      return Boolean(connection?.saveData || ['slow-2g', '2g', '3g'].includes(connection?.effectiveType ?? ''))
    },
    onRevokedScopes: scopes => { if (scopes.length) void storageRegistry.purgeScope(scope) },
  })
}

export function SyncProvider({ children, engineFactory = defaultEngineFactory }: PropsWithChildren<{ engineFactory?: SyncEngineFactory }>) {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  const [engine, setEngine] = useState<SyncEngineLike | null>(null)
  const [state, setState] = useState<SyncState>(DEFAULT_STATE)
  const telemetry = useRef<ClientTelemetry | null>(null)
  const startedAt = useRef(0)
  const park = parkId == null ? 'all' : String(parkId)

  useEffect(() => {
    startedAt.current = typeof performance === 'undefined' ? 0 : performance.now()
    telemetry.current = new ClientTelemetry()
    return () => { telemetry.current?.dispose(); telemetry.current = null }
  }, [])
  useEffect(() => { telemetry.current?.record('queue_length', state.pending) }, [state.pending])
  useEffect(() => {
    setServiceWorkerSyncState(user && !engine ? null : state)
  }, [engine, state, user])
  useEffect(() => () => setServiceWorkerSyncState(DEFAULT_STATE), [])

  useEffect(() => {
    if (!user) { setEngine(null); setState(DEFAULT_STATE); return }
    let active = true
    let current: SyncEngineLike | null = null
    let unsubscribe: (() => void) | null = null
    void engineFactory({ accountId: user.id, park, user })
      .then(created => {
        if (!active) { created.dispose(); return }
        current = created
        setEngine(created)
        setState(created.getState())
        telemetry.current?.record('startup_ms', Math.max(0, (typeof performance === 'undefined' ? 0 : performance.now()) - startedAt.current))
        unsubscribe = created.subscribe(() => setState(created.getState()))
        created.start()
      })
      .catch(() => {
        if (active) setState({ status: 'attention', pending: 0, conflicts: 0 })
      })
    return () => {
      active = false
      unsubscribe?.()
      current?.dispose()
      setEngine(null)
      setState(DEFAULT_STATE)
    }
  }, [engineFactory, park, user])

  const value = useMemo<SyncContextValue>(() => ({
    state,
    actionTrackingReady: engine !== null,
    enqueueAction: input => engine?.enqueueAction?.(input) ?? Promise.reject(new Error('sync_not_ready')),
    enqueueOptimistic: (input, projection) => engine?.enqueueOptimistic?.(input, projection) ?? Promise.reject(new Error('sync_not_ready')),
    enqueueMedia: input => engine?.enqueueMedia?.(input) ?? Promise.reject(new Error('sync_not_ready')),
    syncNow: reason => engine?.syncNow?.(reason ?? 'manual') ?? Promise.resolve(false),
    cancelAction: id => engine?.cancelAction?.(id) ?? Promise.resolve(),
    resolveConflict: (id, revision) => engine?.resolveConflict?.(id, revision) ?? Promise.resolve(),
    findAction: (resourceId, action) => engine?.findAction?.(resourceId, action) ?? Promise.resolve(undefined),
    subscribeAction: (id, listener) => engine?.subscribeAction?.(id, listener) ?? (() => undefined),
    getProjection: (resourceType, resourceId) => engine?.getProjection?.(resourceType, resourceId),
    subscribeProjection: listener => engine?.subscribeProjection?.(listener) ?? (() => undefined),
  }), [engine, state])
  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>
}

export function useSync(): SyncContextValue {
  const value = useContext(SyncContext)
  if (!value) throw new Error('useSync must be used inside SyncProvider')
  return value
}

export function useOptionalSync(): SyncContextValue | null {
  return useContext(SyncContext)
}
