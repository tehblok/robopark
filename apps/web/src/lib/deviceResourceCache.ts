import type { User } from '../api'
import { IndexedResourceStore } from './indexedResourceStore'
import type { OfflineScope } from '../pwa/offlineTypes'

const DEVICE_CACHE_SCHEMA = 1
let active: IndexedResourceStore | null = null
let fingerprint = ''
let activation = 0

function userFingerprint(user: User, park: string): string {
  return JSON.stringify({
    account: user.id,
    role: user.role,
    permissions: [...(user.permissions ?? [])].sort(),
    parks: user.parks.map(park => park.id).sort((a, b) => a - b),
    park,
  })
}

export function offlineScopeForUser(user: User, park = 'all'): OfflineScope {
  return {
    account: String(user.id),
    role: user.role,
    permissions: [...(user.permissions ?? [])].sort().join(','),
    park,
    schema: DEVICE_CACHE_SCHEMA,
  }
}

export async function activateDeviceResourceCache(user: User, park = 'all'): Promise<void> {
  const nextFingerprint = userFingerprint(user, park)
  if (active && fingerprint === nextFingerprint) return
  const generation = ++activation
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
      permissions: `${user.role}:${[...(user.permissions ?? [])].sort().join(',')}`,
      park,
      schema: DEVICE_CACHE_SCHEMA,
    })
  } catch {
    return
  }
  if (generation !== activation) { opened.close(); return }
  active = opened
}

export function purgeDeviceResourceCache(): Promise<void> {
  activation += 1
  const current = active
  active = null
  fingerprint = ''
  if (!current) return Promise.resolve()
  return current.purge().catch(() => undefined).finally(() => current.close())
}

export function currentDeviceResourceCache(): IndexedResourceStore | null { return active }
