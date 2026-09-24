import { isOfflineScopeRetired, retireOfflineScope } from './offlineDb'
import type { OfflineScope } from './offlineTypes'
import { quarantineReportPhotoDraftsForScope, restoreReportPhotoDraftsForScope } from '../domains/reports/reportPhotoDrafts'

export type StorageNamespace = {
  name: string
  kind: 'indexedDB' | 'localStorage'
  owner: string
  schema: number
  retention: string
  scope: 'account-role-permissions-park' | 'account-park' | 'account' | 'ephemeral-global' | 'device'
}

// Prefix names in this inventory include every key written below that prefix.
const namespaces: readonly StorageNamespace[] = [
  { name: 'robopark-offline', kind: 'indexedDB', owner: 'pwa', schema: 2, retention: '14d projections; pending until acknowledged', scope: 'account-role-permissions-park' },
  { name: 'robopark-resource-cache', kind: 'indexedDB', owner: 'resources', schema: 1, retention: 'cache policy', scope: 'account-role-permissions-park' },
  { name: 'robopark-task-attachment-cache', kind: 'indexedDB', owner: 'tracker', schema: 1, retention: '5m; 32MiB', scope: 'account' },
  { name: 'robopark-share-inbox', kind: 'indexedDB', owner: 'pwa', schema: 1, retention: '24h; 10 drafts; purge on logout', scope: 'ephemeral-global' },
  { name: 'robopark-report-drafts-v1', kind: 'indexedDB', owner: 'reports', schema: 2, retention: 'until submitted; quarantined on access change', scope: 'account-role-permissions-park' },
  { name: 'robopark:res:', kind: 'localStorage', owner: 'resources', schema: 1, retention: 'legacy; removed at bootstrap', scope: 'account-role-permissions-park' },
  { name: 'robopark:report-draft:', kind: 'localStorage', owner: 'reports', schema: 1, retention: 'until submitted', scope: 'account-park' },
  { name: 'robopark:retired-draft:', kind: 'localStorage', owner: 'reports', schema: 1, retention: 'until exact scope reauthorized', scope: 'account-role-permissions-park' },
  { name: 'robopark:handoff:', kind: 'localStorage', owner: 'tracker', schema: 1, retention: 'until submitted; archived by authorization scope', scope: 'account-role-permissions-park' },
  { name: 'robopark:comment-draft:', kind: 'localStorage', owner: 'tracker', schema: 1, retention: 'until submitted; archived by authorization scope', scope: 'account-role-permissions-park' },
  { name: 'robopark.recentRobots.v2.', kind: 'localStorage', owner: 'robots', schema: 2, retention: 'until logout', scope: 'account' },
  { name: 'robopark.recentRobots', kind: 'localStorage', owner: 'robots', schema: 1, retention: 'legacy; purge on auth transition', scope: 'ephemeral-global' },
  { name: 'robopark:interface:', kind: 'localStorage', owner: 'interface', schema: 1, retention: 'legacy', scope: 'account' },
  { name: 'robopark:panel:', kind: 'localStorage', owner: 'layout', schema: 1, retention: 'until reset', scope: 'device' },
  { name: 'robopark-theme', kind: 'localStorage', owner: 'theme', schema: 1, retention: 'until reset', scope: 'device' },
  { name: 'robopark-density', kind: 'localStorage', owner: 'theme', schema: 1, retention: 'until reset', scope: 'device' },
  { name: 'robopark.lastUsername', kind: 'localStorage', owner: 'login', schema: 1, retention: 'until reset', scope: 'device' },
] as const

const RETIRED_PREFIX = 'robopark:retired-draft:'

function scopeToken(scope: OfflineScope): string {
  return encodeURIComponent(JSON.stringify([scope.account, scope.principal ?? null, scope.role, scope.permissions, scope.park, scope.parkAccess ?? null, scope.schema]))
}

function storage(): Storage | null {
  try { return typeof window === 'undefined' ? null : window.localStorage } catch { return null }
}

function archiveKey(scope: OfflineScope, key: string): string {
  return `${RETIRED_PREFIX}${scopeToken(scope)}:${encodeURIComponent(key)}`
}

function scopedDraftKeys(scope: OfflineScope, target: Storage): string[] {
  const keys = [`robopark:report-draft:${scope.account}:${scope.park}`]
  if (!scope.principal) return keys
  const handoffPrefix = 'robopark:handoff:v1:'
  const commentPrefix = `robopark:comment-draft:v1:${encodeURIComponent(scope.principal)}:`
  for (let index = 0; index < target.length; index++) {
    const key = target.key(index)
    if (!key) continue
    if (key.startsWith(commentPrefix)) { keys.push(key); continue }
    if (!key.startsWith(handoffPrefix)) continue
    try {
      const parsed: unknown = JSON.parse(key.slice(handoffPrefix.length))
      if (Array.isArray(parsed) && parsed[0] === scope.principal) keys.push(key)
    } catch { /* Unknown key format is not claimed for this scope. */ }
  }
  return keys
}

function quarantineDrafts(scope: OfflineScope): void {
  const target = storage()
  if (!target) return
  try {
    for (const key of scopedDraftKeys(scope, target)) {
      const value = target.getItem(key)
      if (value === null) continue
      const archivedKey = archiveKey(scope, key)
      if (target.getItem(archivedKey) !== null) continue
      // Commit each archive before hiding its active key. A failed write leaves the draft untouched.
      target.setItem(archivedKey, value)
      target.removeItem(key)
    }
  } catch { /* Do not delete a draft when storage is denied or full. */ }
}

function restoreDrafts(scope: OfflineScope): void {
  const target = storage()
  if (!target) return
  try {
    const prefix = `${RETIRED_PREFIX}${scopeToken(scope)}:`
    const archivedKeys = Array.from({ length: target.length }, (_, index) => target.key(index))
      .filter((key): key is string => Boolean(key?.startsWith(prefix)))
    for (const archivedKey of archivedKeys) {
      const key = decodeURIComponent(archivedKey.slice(prefix.length))
      const value = target.getItem(archivedKey)
      if (value === null || target.getItem(key) !== null) continue
      target.setItem(key, value)
      target.removeItem(archivedKey)
    }
  } catch { /* Leave the recoverable archive in place. */ }
}

export const storageRegistry = {
  inventory(): StorageNamespace[] { return namespaces.map(item => ({ ...item })) },
  isRetired(scope: OfflineScope): boolean { return isOfflineScopeRetired(scope) },
  activateScope(scope: OfflineScope): void { restoreDrafts(scope); void restoreReportPhotoDraftsForScope(scope).catch(() => {}) },
  async purgeScope(scope: OfflineScope): Promise<'retired'> {
    quarantineDrafts(scope)
    retireOfflineScope(scope)
    await quarantineReportPhotoDraftsForScope(scope).catch(() => {})
    return 'retired'
  },
}

export const purgeScope = (scope: OfflineScope) => storageRegistry.purgeScope(scope)
