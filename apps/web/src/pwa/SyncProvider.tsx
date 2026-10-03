import { useEffect, useMemo, useRef, useState, type PropsWithChildren } from 'react'
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
import { runLocalWork, setServiceWorkerSyncState } from './registerServiceWorker'
import { SyncContext, type SyncContextValue } from './syncContext'

export type { SyncContextValue } from './syncContext'

export type SyncEngineLike = {
  start(): void
  dispose(): void
  subscribe(listener: () => void): () => void
  getState(): SyncState
  enqueueAction?(input: OfflineActionInput): Promise<unknown>
  enqueueOptimistic?(input: OfflineActionInput, projection?: unknown): Promise<unknown>
  enqueueMedia?(input: OfflineMediaInput, dependentAction?: OfflineActionInput): Promise<unknown>
  syncNow?(reason: string): Promise<boolean>
  cancelAction?(id: string): Promise<void>
  resolveConflict?(id: string, baseRevision: string | null): Promise<void>
  findAction?(resourceId: string, action: string): Promise<OfflineAction | undefined>
  listActions?(): Promise<OfflineAction[]>
  listMedia?(): Promise<OfflineMedia[]>
  subscribeAction?(id: string, listener: (action: OfflineAction | undefined) => void): () => void
  getProjection?(resourceType: string, resourceId: string): unknown
  subscribeProjection?(listener: () => void): () => void
}
export type SyncEngineFactory = (options: { accountId: number, park: string, user: ReturnType<typeof useAuth>['user'], onRevokedScopes?: (scopes: string[]) => void }) => Promise<SyncEngineLike>

const DEFAULT_STATE: SyncState = { status: 'idle', pending: 0, conflicts: 0 }

export function SyncContextProvider({ children, value }: PropsWithChildren<{ value: SyncContextValue }>) {
  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>
}

async function defaultEngineFactory(options: Parameters<SyncEngineFactory>[0]): Promise<SyncEngineLike> {
  if (!options.user) throw new Error('sync_user_required')
  const scope = offlineScopeForUser(options.user, options.park)
  const sendBatch = (batch: Parameters<typeof api.syncBatch>[0], signal?: AbortSignal) => api.syncBatch(batch, signal)
  const deviceId = `account-${options.accountId}`
  const sendMedia = (media: OfflineMedia, signal?: AbortSignal) => uploadMedia(
    { id: media.id, actionId: media.actionId, deviceId, blob: media.blob, mimeType: media.mimeType, sha256: media.sha256, name: media.name },
    {
      create: (input, requestSignal) => api.createMediaUpload({ ...input, issue_key: media.issueKey }, requestSignal),
      putChunk: (uploadId, offset, chunk, sha256, requestSignal) => api.putMediaChunk(uploadId, offset, chunk, sha256, requestSignal),
      complete: (uploadId, requestSignal) => api.completeMediaUpload(uploadId, requestSignal),
    },
    { signal },
  ).then(() => undefined)
  let db: Awaited<ReturnType<typeof openOfflineDb>>
  try {
    db = await openOfflineDb(scope)
  } catch {
    return new NetworkOnlySyncEngine({ deviceId, sendBatch, uploadMedia: sendMedia, onRevokedScopes: options.onRevokedScopes })
  }
  try {
    await db.cleanup({ maxBytes: await estimateOfflineBudget() })
  } catch (error) {
    if (!(error instanceof OfflineStorageFullError)) {
      db.close()
      return new NetworkOnlySyncEngine({ deviceId, sendBatch, uploadMedia: sendMedia, onRevokedScopes: options.onRevokedScopes })
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
    deviceId,
    sendBatch,
    uploadMedia: sendMedia,
    weakLink: () => {
      const connection = (navigator as Navigator & { connection?: { effectiveType?: string, saveData?: boolean } }).connection
      return Boolean(connection?.saveData || ['slow-2g', '2g', '3g'].includes(connection?.effectiveType ?? ''))
    },
    onRevokedScopes: options.onRevokedScopes,
  })
}

export function SyncProvider({ children, engineFactory = defaultEngineFactory }: PropsWithChildren<{ engineFactory?: SyncEngineFactory }>) {
  const { user, refreshUser } = useAuth()
  const { parkId } = useParkScope()
  const [engineRecord, setEngineRecord] = useState<{ engine: SyncEngineLike; scopeKey: string } | null>(null)
  const [state, setState] = useState<SyncState>(DEFAULT_STATE)
  const telemetry = useRef<ClientTelemetry | null>(null)
  const startedAt = useRef(0)
  const park = parkId == null ? 'all' : String(parkId)
  const desiredScopeKey = user ? JSON.stringify(offlineScopeForUser(user, park)) : null
  const engine = engineRecord?.scopeKey === desiredScopeKey ? engineRecord.engine : null

  useEffect(() => {
    startedAt.current = typeof performance === 'undefined' ? 0 : performance.now()
    telemetry.current = new ClientTelemetry()
    return () => { telemetry.current?.dispose(); telemetry.current = null }
  }, [])
  useEffect(() => { telemetry.current?.record('queue_length', state.pending) }, [state.pending])
  useEffect(() => {
    setServiceWorkerSyncState(user && engine ? state : null, user?.id)
  }, [engine, state, user])
  useEffect(() => () => setServiceWorkerSyncState(null), [])

  useEffect(() => {
    if (!user) { setEngineRecord(null); setState(DEFAULT_STATE); return }
    let active = true
    let revoked = false
    let current: SyncEngineLike | null = null
    let unsubscribe: (() => void) | null = null
    const onRevokedScopes = (scopes: string[]) => {
      if (!active || revoked || !scopes.length) return
      revoked = true
      current?.dispose()
      setEngineRecord(null)
      setState({ status: 'attention', pending: 0, conflicts: 1 })
      void storageRegistry.purgeScope(offlineScopeForUser(user, park)).catch(() => {})
      window.dispatchEvent(new CustomEvent('robopark:authorization-failure', { detail: { status: 403 } }))
      void refreshUser().catch(() => {})
    }
    void engineFactory({ accountId: user.id, park, user, onRevokedScopes })
      .then(created => {
        if (!active) { created.dispose(); return }
        current = created
        setEngineRecord({ engine: created, scopeKey: JSON.stringify(offlineScopeForUser(user, park)) })
        setServiceWorkerSyncState(created.getState(), user.id)
        setState(created.getState())
        telemetry.current?.record('startup_ms', Math.max(0, (typeof performance === 'undefined' ? 0 : performance.now()) - startedAt.current))
        unsubscribe = created.subscribe(() => {
          setServiceWorkerSyncState(created.getState(), user.id)
          setState(created.getState())
        })
        created.start()
      })
      .catch(() => {
        if (active) setState({ status: 'attention', pending: 0, conflicts: 0 })
      })
    return () => {
      active = false
      unsubscribe?.()
      current?.dispose()
      setEngineRecord(null)
      setState(DEFAULT_STATE)
    }
  }, [engineFactory, park, refreshUser, user])

  const value = useMemo<SyncContextValue>(() => ({
    state,
    actionTrackingReady: engine !== null,
    scopeKey: engine ? desiredScopeKey : null,
    enqueueAction: input => runLocalWork(() => engine?.enqueueAction?.(input) ?? Promise.reject(new Error('sync_not_ready'))),
    enqueueOptimistic: (input, projection) => runLocalWork(() => engine?.enqueueOptimistic?.(input, projection) ?? Promise.reject(new Error('sync_not_ready'))),
    enqueueMedia: (input, dependentAction) => runLocalWork(() => engine?.enqueueMedia?.(input, dependentAction) ?? Promise.reject(new Error('sync_not_ready'))),
    syncNow: reason => engine?.syncNow?.(reason ?? 'manual') ?? Promise.resolve(false),
    cancelAction: id => engine?.cancelAction?.(id) ?? Promise.resolve(),
    resolveConflict: (id, revision) => engine?.resolveConflict?.(id, revision) ?? Promise.resolve(),
    findAction: (resourceId, action) => engine?.findAction?.(resourceId, action) ?? Promise.resolve(undefined),
    listActions: () => engine?.listActions?.() ?? Promise.resolve([]),
    listMedia: () => engine?.listMedia?.() ?? Promise.resolve([]),
    subscribeAction: (id, listener) => engine?.subscribeAction?.(id, listener) ?? (() => undefined),
    getProjection: (resourceType, resourceId) => engine?.getProjection?.(resourceType, resourceId),
    subscribeProjection: listener => engine?.subscribeProjection?.(listener) ?? (() => undefined),
  }), [desiredScopeKey, engine, state])
  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>
}
