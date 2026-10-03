import { createContext, useContext } from 'react'
import type { OfflineAction, OfflineMedia } from './offlineTypes'
import type { OfflineActionInput, OfflineMediaInput, SyncState } from './syncEngine'

export type SyncContextValue = {
  state: SyncState
  actionTrackingReady?: boolean
  enqueueAction(input: OfflineActionInput): Promise<unknown>
  enqueueOptimistic?(input: OfflineActionInput, projection?: unknown): Promise<unknown>
  enqueueMedia(input: OfflineMediaInput, dependentAction?: OfflineActionInput): Promise<unknown>
  syncNow(reason?: string): Promise<boolean>
  cancelAction(id: string): Promise<void>
  resolveConflict(id: string, baseRevision: string | null): Promise<void>
  findAction(resourceId: string, action: string): Promise<OfflineAction | undefined>
  listActions?(): Promise<OfflineAction[]>
  listMedia?(): Promise<OfflineMedia[]>
  scopeKey?: string | null
  subscribeAction(id: string, listener: (action: OfflineAction | undefined) => void): () => void
  getProjection?(resourceType: string, resourceId: string): unknown
  subscribeProjection?(listener: () => void): () => void
}

export const SyncContext = createContext<SyncContextValue | null>(null)

export function useSync(): SyncContextValue {
  const value = useContext(SyncContext)
  if (!value) throw new Error('useSync must be used inside SyncProvider')
  return value
}

export function useOptionalSync(): SyncContextValue | null {
  return useContext(SyncContext)
}
