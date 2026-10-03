import type { User } from '../api'
import { IndexedResourceStore } from './indexedResourceStore'
import type { OfflineScope } from '../pwa/offlineTypes'

const DEVICE_CACHE_SCHEMA = 1
let active: IndexedResourceStore | null = null
let fingerprint = ''
let activation = 0
let operationQueue: Promise<void> = Promise.resolve()

function serialize(operation: () => Promise<void>): Promise<void> {
  const result = operationQueue.then(operation, operation)
  operationQueue = result.catch(() => undefined)
  return result
}

function userFingerprint(user: User): string {
  return JSON.stringify({
    account: user.id,
    principal: user.username,
    accessStatus: user.access_status,
    role: user.role,
    permissions: [...(user.permissions ?? [])].sort(),
    parks: user.parks.map(park => park.id).sort((a, b) => a - b),
  })
}

export function offlineScopeForUser(user: User, park = 'all'): OfflineScope {
  return {
    account: String(user.id),
    principal: user.username,
    parkAccess: user.parks.map(park => park.id).sort((a, b) => a - b).join(','),
    role: user.role,
    permissions: [...(user.permissions ?? [])].sort().join(','),
    park,
    schema: DEVICE_CACHE_SCHEMA,
  }
}

export async function activateDeviceResourceCache(user: User, _park = 'all'): Promise<void> {
  // Selection is a resource-key concern, not an authorization change. Keep
  // bounded snapshots while moving between permitted parks and fleet views.
  const nextFingerprint = userFingerprint(user)
  const generation = ++activation
  return serialize(async () => {
    if (generation !== activation) return
    if (active && fingerprint === nextFingerprint) return
    const previous = active
    active = null
    fingerprint = nextFingerprint
    if (previous) {
      try {
        await previous.purge()
      } catch {
        previous.close()
        return
      }
      previous.close()
    }
    if (typeof indexedDB === 'undefined') return
    let opened: IndexedResourceStore
    try {
      opened = await IndexedResourceStore.open({
        account: String(user.id),
        principal: user.username,
        accessStatus: user.access_status,
        role: user.role,
        permissions: [...(user.permissions ?? [])].sort().join(','),
        park: 'all',
        parkAccess: user.parks.map(park => park.id).sort((a, b) => a - b).join(','),
        schema: DEVICE_CACHE_SCHEMA,
      })
    } catch {
      return
    }
    if (generation !== activation) { opened.close(); return }
    active = opened
  })
}

export function purgeDeviceResourceCache(): Promise<void> {
  activation += 1
  const current = active
  active = null
  fingerprint = ''
  return serialize(async () => {
    if (current) await current.purge().catch(() => undefined).finally(() => current.close())
    else await IndexedResourceStore.clearAll().catch(() => undefined)
  })
}

export function currentDeviceResourceCache(): IndexedResourceStore | null { return active }

/** Other tabs own durable cleanup; only retire this tab's handles and late reads. */
export function suspendDeviceResourceCache(): void {
  activation += 1
  active?.close()
  active = null
  fingerprint = ''
}
