import type { NotificationEvent } from '../api'
import { currentDeviceResourceCache } from '../lib/deviceResourceCache'
import { classifyApiError } from '../shared/api/classifyApiError'

const KEY = 'notifications:inbox:v1'
const RETENTION = 24 * 60 * 60 * 1000

function fresh(items: NotificationEvent[]): NotificationEvent[] {
  return items.filter(item => Number.isFinite(Date.parse(item.created_at)) && Date.parse(item.created_at) + RETENTION > Date.now()).slice(0, 500)
}

/** Uses the existing account/permission-scoped, AES-GCM desktop resource cache. */
export async function loadNotificationInbox(fetchInbox: () => Promise<NotificationEvent[]>): Promise<{ items: NotificationEvent[]; offline: boolean }> {
  const store = currentDeviceResourceCache()
  const generation = store?.captureGeneration()
  const current = () => currentDeviceResourceCache() === store && (!store || store.isGenerationCurrent(generation!))
  try {
    const items = fresh(await fetchInbox())
    if (!current()) throw new Error('notification_account_changed')
    await store?.set(KEY, items, generation, Date.now(), RETENTION).catch(() => undefined)
    if (!current()) throw new Error('notification_account_changed')
    return { items, offline: false }
  } catch (error) {
    const kind = classifyApiError(error, '').kind
    if (!current() || !['offline', 'timeout', 'server'].includes(kind)) throw error
    const cached = await store?.get<NotificationEvent[]>(KEY).catch(() => undefined)
    if (!current() || !Array.isArray(cached)) throw error
    return { items: fresh(cached), offline: true }
  }
}


export async function rememberNotificationRead(id: string, readAt: string): Promise<void> {
  const store = currentDeviceResourceCache()
  if (!store) return
  const generation = store.captureGeneration()
  const cached = await store.get<NotificationEvent[]>(KEY).catch(() => undefined)
  if (!Array.isArray(cached) || currentDeviceResourceCache() !== store || !store.isGenerationCurrent(generation)) return
  await store.set(KEY, fresh(cached).map(item => item.id === id ? { ...item, read_at: readAt } : item), generation, Date.now(), RETENTION).catch(() => undefined)
}
