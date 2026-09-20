import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { useParkScope } from '../app/park/parkScope'
import { offlineScopeForUser } from '../lib/deviceResourceCache'
import { openOfflineDb, purgeOfflineScope } from './offlineDb'
import { SyncCoordinator } from './syncCoordinator'
import { SyncEngine, type OfflineActionInput, type SyncState } from './syncEngine'

export type SyncEngineLike = Pick<SyncEngine, 'start' | 'dispose' | 'subscribe' | 'getState'> & Partial<Pick<SyncEngine, 'enqueueAction' | 'syncNow' | 'cancelAction' | 'resolveConflict'>>
export type SyncEngineFactory = (options: { accountId: number, park: string, user: ReturnType<typeof useAuth>['user'] }) => Promise<SyncEngineLike>

type SyncContextValue = {
  state: SyncState
  enqueueAction(input: OfflineActionInput): Promise<unknown>
  syncNow(reason?: string): Promise<boolean>
  cancelAction(id: string): Promise<void>
  resolveConflict(id: string, baseRevision: string | null): Promise<void>
}

const DEFAULT_STATE: SyncState = { status: 'idle', pending: 0, conflicts: 0 }
const SyncContext = createContext<SyncContextValue | null>(null)

async function defaultEngineFactory(options: Parameters<SyncEngineFactory>[0]): Promise<SyncEngineLike> {
  if (!options.user) throw new Error('sync_user_required')
  const db = await openOfflineDb(offlineScopeForUser(options.user, options.park))
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
    sendBatch: (batch, signal) => api.syncBatch(batch, signal),
    weakLink: () => {
      const connection = (navigator as Navigator & { connection?: { effectiveType?: string, saveData?: boolean } }).connection
      return Boolean(connection?.saveData || ['slow-2g', '2g', '3g'].includes(connection?.effectiveType ?? ''))
    },
    onRevokedScopes: scopes => { if (scopes.length) void purgeOfflineScope() },
  })
}

export function SyncProvider({ children, engineFactory = defaultEngineFactory }: PropsWithChildren<{ engineFactory?: SyncEngineFactory }>) {
  const { user } = useAuth()
  const { parkId } = useParkScope()
  const [engine, setEngine] = useState<SyncEngineLike | null>(null)
  const [state, setState] = useState<SyncState>(DEFAULT_STATE)
  const park = parkId == null ? 'all' : String(parkId)

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
    enqueueAction: input => engine?.enqueueAction?.(input) ?? Promise.reject(new Error('sync_not_ready')),
    syncNow: reason => engine?.syncNow?.(reason ?? 'manual') ?? Promise.resolve(false),
    cancelAction: id => engine?.cancelAction?.(id) ?? Promise.resolve(),
    resolveConflict: (id, revision) => engine?.resolveConflict?.(id, revision) ?? Promise.resolve(),
  }), [engine, state])
  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>
}

export function useSync(): SyncContextValue {
  const value = useContext(SyncContext)
  if (!value) throw new Error('useSync must be used inside SyncProvider')
  return value
}
