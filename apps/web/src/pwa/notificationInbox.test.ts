import { IDBFactory } from 'fake-indexeddb'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApiError, type User, type NotificationEvent } from '../api'
import { activateDeviceResourceCache, purgeDeviceResourceCache } from '../lib/deviceResourceCache'
import { loadNotificationInbox, rememberNotificationRead } from './notificationInbox'
const user = (id: number): User => ({ id, username: `user-${id}`, role: 'operator', access_status: 'approved', permissions: [], parks: [] })
const event = (hours = 0): NotificationEvent => ({ id: 'n1', event_type: 'report', park_id: 1, protected_text: 'Отчёт', read_at: null, created_at: new Date(Date.now() - hours * 3600000).toISOString() })
beforeEach(() => vi.stubGlobal('indexedDB', new IDBFactory()))
afterEach(async () => { await purgeDeviceResourceCache(); vi.unstubAllGlobals() })
it('keeps a bounded scoped offline inbox but never resurrects expired events', async () => {
  await activateDeviceResourceCache(user(1))
  expect((await loadNotificationInbox(async () => [event(), event(25)])).items).toHaveLength(1)
  const cached = await loadNotificationInbox(async () => { throw new TypeError('offline') })
  expect(cached.offline).toBe(true); expect(cached.items).toHaveLength(1)
})
it('does not use offline snapshots to bypass an online denial', async () => {
  await activateDeviceResourceCache(user(1))
  await loadNotificationInbox(async () => [event()])
  await expect(loadNotificationInbox(async () => { throw new ApiError(403, 'forbidden') })).rejects.toMatchObject({ status: 403 })
})
it('does not expose the previous account inbox or a late response', async () => {
  await activateDeviceResourceCache(user(1))
  await loadNotificationInbox(async () => [event()])
  let resolve!: (value: NotificationEvent[]) => void
  const pending = loadNotificationInbox(() => new Promise(done => { resolve = done }))
  const rejected = expect(pending).rejects.toThrow('notification_account_changed')
  await activateDeviceResourceCache(user(2)); resolve([event()]); await rejected
  await expect(loadNotificationInbox(async () => { throw new TypeError('offline') })).rejects.toThrow('offline')
})

it('persists a confirmed read for the offline inbox', async () => {
  await activateDeviceResourceCache(user(1))
  await loadNotificationInbox(async () => [event()])
  const now = new Date().toISOString()
  await rememberNotificationRead('n1', now)
  const result = await loadNotificationInbox(async () => { throw new TypeError('offline') })
  expect(result.items[0].read_at).toBe(now)
})
