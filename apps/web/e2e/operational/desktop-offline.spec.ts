import { expect, test } from '../support/persistentWebKit'
import { installOperational, issue } from './fixtures'

const instanceId = '6f17d720-9fab-48da-8dd5-9f3d270b2878'
const storageKey = Buffer.alloc(32, 17).toString('base64')

test.beforeEach(async ({ page, baseURL }) => {
  test.skip(!baseURL || new URL(baseURL).port !== '18444', 'Desktop contract requires the fixed loopback port')
  await page.addInitScript(id => {
    Object.defineProperty(window, '__ROBOPARK_DESKTOP__', { configurable: true, value: { version: 1, instanceId: id } })
  }, instanceId)
  await page.route('**/__desktop_offline_probe__', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Desktop offline probe</title>' }))
})

test('desktop encrypted storage preserves snapshots and action receipts across reload', async ({ page }) => {
  await page.route('**/__robopark/storage-key', route => route.fulfill({ json: { key: storageKey } }))
  await page.goto('/__desktop_offline_probe__')
  const raw = await page.evaluate(async () => {
    const resourcesUrl = '/src/lib/indexedResourceStore.ts'
    const offlineUrl = '/src/pwa/offlineDb.ts'
    const { IndexedResourceStore } = await import(resourcesUrl) as typeof import('../../src/lib/indexedResourceStore')
    const { openOfflineDb, OFFLINE_DATABASE_NAME } = await import(offlineUrl) as typeof import('../../src/pwa/offlineDb')
    const resourceScope = { account: '7', principal: 'operator', accessStatus: 'approved', role: 'operator', permissions: 'tracker.read', park: 'all', parkAccess: '7', schema: 1 }
    const store = await IndexedResourceStore.open(resourceScope)
    await store.set('offline-preparation:7:state', { secret: 'prepared-cold-data', preparedAt: Date.now() }, store.captureGeneration(), Date.now(), 72 * 60 * 60 * 1000)
    store.close()
    const offlineScope = { account: '7', principal: 'operator', role: 'operator', permissions: 'tracker.read', park: '7', parkAccess: '7', schema: 1 }
    const db = await openOfflineDb(offlineScope)
    await db.putQueuedAction({
      id: 'queued-desktop-write', deviceId: 'mac-install', resourceType: 'task', resourceId: 'ROBOPARK-42', action: 'comment',
      idempotencyKey: 'desktop-idempotency', baseRevision: null, dependencies: [], payload: { text: 'queued-private-comment' },
      state: 'ready', createdAt: 1, updatedAt: 1,
    })
    db.close()
    const request = indexedDB.open(OFFLINE_DATABASE_NAME, 2)
    const opened = await new Promise<IDBDatabase>((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error) })
    const records = await new Promise<unknown[]>((resolve, reject) => {
      const get = opened.transaction('actions').objectStore('actions').getAll()
      get.onsuccess = () => resolve(get.result); get.onerror = () => reject(get.error)
    })
    opened.close()
    return JSON.stringify(records)
  })
  expect(raw).not.toContain('queued-private-comment')

  await page.reload()
  const restored = await page.evaluate(async () => {
    const resourcesUrl = '/src/lib/indexedResourceStore.ts'
    const offlineUrl = '/src/pwa/offlineDb.ts'
    const { IndexedResourceStore } = await import(resourcesUrl) as typeof import('../../src/lib/indexedResourceStore')
    const { openOfflineDb } = await import(offlineUrl) as typeof import('../../src/pwa/offlineDb')
    const store = await IndexedResourceStore.open({ account: '7', principal: 'operator', accessStatus: 'approved', role: 'operator', permissions: 'tracker.read', park: 'all', parkAccess: '7', schema: 1 })
    const prepared = await store.get<{ secret: string }>('offline-preparation:7:state')
    store.close()
    const db = await openOfflineDb({ account: '7', principal: 'operator', role: 'operator', permissions: 'tracker.read', park: '7', parkAccess: '7', schema: 1 })
    const queued = await db.getAction('queued-desktop-write')
    if (queued) await db.putAction({ ...queued, state: 'confirmed', result: { reconnected: true }, updatedAt: 2 })
    const confirmed = await db.getAction('queued-desktop-write')
    db.close()
    return { prepared, queued, confirmed }
  })
  expect(restored.prepared).toEqual(expect.objectContaining({ secret: 'prepared-cold-data' }))
  expect(restored.queued).toEqual(expect.objectContaining({ state: 'ready', payload: { text: 'queued-private-comment' } }))
  expect(restored.confirmed).toEqual(expect.objectContaining({ state: 'confirmed', result: { reconnected: true } }))
})

test('desktop workspace cold reloads during outage and durably reconciles a UI comment on reconnect', async ({ page }) => {
  test.setTimeout(60_000)
  const submitted: string[] = []
  const repair = { ...issue, claim: { park_id: 7 }, workflow: {
    owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
    display_status: 'in_progress' as const, sync_state: 'synced' as const, has_current_cycle_comment: false,
  } }
  await page.route('**/__robopark/storage-key', route => route.fulfill({ json: { key: storageKey } }))
  await installOperational(page, { issue: repair, realTime: true, routes: [{
    method: 'POST', path: '/api/sync/batch', handler: async request => {
      const body = await request.json() as { actions: { client_action_id: string }[] }
      submitted.push(...body.actions.map(action => action.client_action_id))
      return { json: { results: body.actions.map(action => ({ client_action_id: action.client_action_id, state: 'confirmed', code: null, result: {} })), deltas: {}, revisions: {}, revoked_scopes: [] } }
    },
  }] })
  let unavailable = false
  // Local assets and the native Keychain endpoint remain reachable even when
  // the remote API is down. This models the desktop proxy, not an offline CDN.
  await page.route(/^http:\/\/127\.0\.0\.1:18444\/api\//, route => unavailable
    ? route.fulfill({ status: 503, json: { detail: 'desktop_server_unavailable' } })
    : route.fallback())
  await page.goto('/work/ROBOPARK-42?park=7')
  await page.getByRole('button', { name: 'История и сообщения' }).click()
  await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toBeVisible()
  expect(await page.evaluate(() => navigator.serviceWorker.controller)).toBeNull()
  unavailable = true
  await page.reload()
  await page.getByRole('button', { name: 'История и сообщения' }).click()
  const composer = page.getByRole('textbox', { name: 'Комментарии', exact: true })
  await expect(composer).toBeVisible()
  await composer.fill('Проверено офлайн в локальном приложении')
  await page.getByRole('button', { name: 'Отправить' }).click()
  await expect(page.getByText('Проверено офлайн в локальном приложении')).toBeVisible()
  expect(submitted).toEqual([])
  await page.reload()
  await page.getByRole('button', { name: 'История и сообщения' }).click()
  await expect(page.getByText('Проверено офлайн в локальном приложении')).toBeVisible()
  unavailable = false
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await expect.poll(() => submitted.length, { timeout: 20_000 }).toBeGreaterThanOrEqual(1)
  await expect.poll(() => page.evaluate(async (id) => {
    const request = indexedDB.open('robopark-offline', 2)
    const db = await new Promise<IDBDatabase>((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error) })
    try {
      const records = await new Promise<{ id: string; state: string }[]>((resolve, reject) => {
        const get = db.transaction('actions').objectStore('actions').getAll()
        get.onsuccess = () => resolve(get.result); get.onerror = () => reject(get.error)
      })
      return records.find(record => record.id === id)?.state
    } finally { db.close() }
  }, submitted[0]), { timeout: 20_000 }).toBe('confirmed')
  // Revalidation may cancel an in-flight response. Retrying the SAME action
  // ID is intentional; no second logical command may be created.
  expect(new Set(submitted).size).toBe(1)
  const acknowledgedAttempts = submitted.length
  await expect(page.getByRole('button', { name: 'Открыть центр синхронизации. Синхронизация выполняется автоматически', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: 'История и сообщения' })).toBeVisible()
  expect(submitted).toHaveLength(acknowledgedAttempts)
})

test('desktop encrypted storage fails closed when the native key is unavailable after reload', async ({ page }) => {
  let available = true
  await page.route('**/__robopark/storage-key', route => available
    ? route.fulfill({ json: { key: storageKey } })
    : route.fulfill({ status: 503, json: { detail: 'key_unavailable' } }))
  await page.goto('/__desktop_offline_probe__')
  await page.evaluate(async () => {
    const moduleUrl = '/src/lib/indexedResourceStore.ts'
    const { IndexedResourceStore } = await import(moduleUrl) as typeof import('../../src/lib/indexedResourceStore')
    const store = await IndexedResourceStore.open({ account: 'key-probe', role: 'operator', permissions: 'tracker.read', park: 'all', schema: 1 })
    await store.set('protected', { secret: 'must-not-fall-back-to-plaintext' })
    store.close()
  })
  available = false
  await page.reload()
  const value = await page.evaluate(async () => {
    const moduleUrl = '/src/lib/indexedResourceStore.ts'
    const { IndexedResourceStore } = await import(moduleUrl) as typeof import('../../src/lib/indexedResourceStore')
    const store = await IndexedResourceStore.open({ account: 'key-probe', role: 'operator', permissions: 'tracker.read', park: 'all', schema: 1 })
    try { return await store.get('protected') } finally { store.close() }
  })
  expect(value).toBeUndefined()
})

test('desktop direct terminal route explains that the server site is required', async ({ page }) => {
  await page.goto('/terminal.html')
  await expect(page.getByRole('heading', { name: 'Терминал недоступен в приложении' })).toBeVisible()
  await expect(page.getByText('Откройте серверный сайт при подключённой сети.')).toBeVisible()
})
