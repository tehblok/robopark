import { randomBytes } from 'node:crypto'
import { type BrowserContext, type Page } from '@playwright/test'
import { expect, test } from '../e2e/support/persistentWebKit'
import { userForRole } from '../e2e/operational/fixtures'

const controlURL = `http://127.0.0.1:${Number(process.env.PLAYWRIGHT_PWA_PORT ?? 4174) + 1}`
async function control(page: Page, path: string, data: object) {
  const response = await page.request.post(`${controlURL}/${path}`, { data })
  expect(response.status()).toBe(204)
}

test.beforeEach(async ({ page, browserName }) => {
  await control(page, 'transport', { offline: false })
  await control(page, 'scenario', {})
  await page.clock.setFixedTime(new Date('2026-09-02T09:05:00Z'))
  if (browserName === 'webkit') await page.addInitScript(() => {
    Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => sessionStorage.getItem('__pwa_offline') !== 'true' })
  })
})
test.afterEach(async ({ page }) => { await control(page, 'transport', { offline: false }) })

async function setOffline(page: Page, context: BrowserContext, offline: boolean) {
  if (context.browser()?.browserType().name() !== 'webkit') { await context.setOffline(offline); return }
  // WebKit's protocol offline emulation rejects even synthetic SW responses
  // (Playwright #42775). Stop the actual origin and emulate the connectivity
  // event; cached documents must still render while uncached HTTP truly fails.
  await control(page, 'transport', { offline })
  await page.evaluate(value => {
    sessionStorage.setItem('__pwa_offline', String(value))
    window.dispatchEvent(new Event(value ? 'offline' : 'online'))
  }, offline)
  if (offline) await expect(page.request.get(`http://127.0.0.1:${Number(process.env.PLAYWRIGHT_PWA_PORT ?? 4174)}/uncached-negative-control`, { timeout: 1000 })).rejects.toThrow()
}

async function waitForActiveWorker(page: Page) {
  await expect.poll(async () => {
    try {
      return await page.evaluate(async () => {
        const registration = await navigator.serviceWorker.getRegistration('/')
        return {
          active: registration?.active?.state ?? null,
          waiting: registration?.waiting?.state ?? null,
          installing: registration?.installing?.state ?? null,
          controlled: Boolean(navigator.serviceWorker.controller),
          url: location.pathname,
        }
      })
    } catch (error) {
      // First activation may reload the document while its state is inspected.
      if (error instanceof Error && /Execution context was destroyed/.test(error.message)) return false
      throw error
    }
  }).toMatchObject({ active: 'activated', controlled: true })
}

for (const role of ['mechanic', 'royal'] as const) for (const width of [390, 1440]) {
  test(`retains ${role} session, park and task after offline PWA restart at ${width}px`, async ({ context, page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.request.post('/__pwa_fixture__/version', { data: { version: 'v1', legacyClient: false } })
    await control(page, 'scenario', { role, signedIn: true })
    await page.goto('/work/ROBOPARK-42?park=7')
    await expect(page.getByRole('heading', { name: 'Проверить переднее левое колесо робота 447', exact: true })).toBeVisible()
    await waitForActiveWorker(page)
    // clients.claim() controls the initial document. An extra reload here can
    // race the safe-activation reload and abort either navigation; the offline
    // reload below is the actual restart exercised by this test.
    await expect(page.getByRole('heading', { name: 'Проверить переднее левое колесо робота 447', exact: true })).toBeVisible()
    // Prepare the offline restart only in a controlled document. A safe first
    // activation can still reload this page; all preparation is idempotent.
    await expect.poll(async () => {
      try {
        return await page.evaluate(async () => {
          const registration = await navigator.serviceWorker.getRegistration('/')
          if (registration?.active?.state !== 'activated' || !navigator.serviceWorker.controller) return false
          const opening = indexedDB.open('robopark-resource-cache')
          const db = await new Promise<IDBDatabase>(resolve => { opening.onsuccess = () => resolve(opening.result) })
          const records = await new Promise<Array<{ key: string; data?: { key?: string } }>>(resolve => {
            const request = db.transaction('resources').objectStore('resources').getAll()
            request.onsuccess = () => resolve(request.result)
          })
          db.close()
          if (!records.some(record => record.key.endsWith(':issue:ROBOPARK-42') && record.data?.key === 'ROBOPARK-42')) return false
          // Emulate a first route loaded before worker control: its code must
          // be available from the install cache even without a runtime hit.
          await Promise.all((await caches.keys()).filter(name => name.startsWith('robopark-runtime-')).map(name => caches.delete(name)))
          return true
        })
      } catch (error) {
        // Retry only the expected first-worker activation navigation.
        if (error instanceof Error && /Execution context was destroyed/.test(error.message)) return false
        throw error
      }
    }).toBe(true)
    await waitForActiveWorker(page)
    await expect(page.getByRole('heading', { name: 'Проверить переднее левое колесо робота 447', exact: true })).toBeVisible()
    await setOffline(page, context, true)
    await page.reload()
    if (width >= 768) await expect(page.getByRole('heading', { name: 'Работа', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Проверить переднее левое колесо робота 447', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toHaveCount(0)
    await expect(page.locator('.rp-sync-center__dot')).toHaveClass(/is-offline/)
    const indicator = await page.locator('.rp-sync-center__trigger').boundingBox()
    expect(indicator).toBeTruthy()
    expect(indicator!.width).toBeLessThanOrEqual(64)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: `/tmp/robopark-offline-session-${role}-${width}.png` })
    await page.getByRole('button', { name: width < 768 ? 'Меню' : 'Ещё', exact: true }).click()
    await page.getByRole('button', { name: 'Выйти', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Работа', exact: true })).toHaveCount(0)
    await setOffline(page, context, false)
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Работа', exact: true })).toHaveCount(0)
  })
}

async function seedScopedOfflineRecords(page: Page) {
  await page.evaluate(async () => {
    const opening = indexedDB.open('robopark-offline', 2)
    opening.onupgradeneeded = () => {
      for (const name of ['actions', 'media', 'entities', 'meta', 'revisions']) {
        const store = opening.result.createObjectStore(name, { keyPath: 'dbId' })
        if (name === 'actions' || name === 'media') store.createIndex('state', 'state')
      }
    }
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      opening.onsuccess = () => resolve(opening.result)
      opening.onerror = () => reject(opening.error ?? new Error('offline database open failed'))
    })
    const transaction = db.transaction(['actions', 'media', 'entities', 'meta', 'revisions'], 'readwrite')
    transaction.objectStore('actions').put({ dbId: 'scope-a\0action', scope: 'scope-a', id: 'action', deviceId: 'pwa-fixture', resourceType: 'issue', resourceId: 'ROBOPARK-42', action: 'comment', idempotencyKey: 'pwa-fixture-comment', baseRevision: null, dependencies: [], payload: { text: 'preserved comment' }, state: 'ready', createdAt: Date.now(), updatedAt: Date.now() })
    transaction.objectStore('media').put({ dbId: 'scope-b\0photo', scope: 'scope-b', id: 'photo', actionId: 'action', issueKey: 'ROBOPARK-42', name: 'private-photo.txt', mimeType: 'text/plain', sizeBytes: 13, sha256: 'a'.repeat(64), state: 'local', blob: new Blob(['private-photo'], { type: 'text/plain' }), createdAt: Date.now(), updatedAt: Date.now() })
    transaction.objectStore('entities').put({ dbId: 'scope-a\0draft', scope: 'scope-a', data: { title: 'preserved draft' } })
    transaction.objectStore('meta').put({ dbId: 'scope-b\0flag', scope: 'scope-b', value: 'preserved meta' })
    transaction.objectStore('revisions').put({ dbId: 'scope-a\0revision', scope: 'scope-a', revision: 'r1' })
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error ?? new Error('offline seed transaction failed'))
      transaction.onabort = () => reject(transaction.error ?? new Error('offline seed transaction aborted'))
    })
    db.close()
  })
}

async function settleOfflineRecord(page: Page, store: 'actions' | 'media', state: string) {
  await page.evaluate(async ({ store, state }) => {
    const opening = indexedDB.open('robopark-offline')
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      opening.onsuccess = () => resolve(opening.result)
      opening.onerror = () => reject(opening.error)
    })
    const key = store === 'actions' ? 'scope-a\0action' : 'scope-b\0photo'
    const reading = db.transaction(store, 'readonly').objectStore(store).get(key)
    const record = await new Promise<Record<string, unknown> & { blob?: Blob }>((resolve, reject) => {
      reading.onsuccess = () => resolve(reading.result)
      reading.onerror = () => reject(reading.error)
    })
    // This fixture writes native IndexedDB records rather than using OfflineDb.
    // Materialize the persisted bytes before replacing its file-backed Blob:
    // WebKit intermittently loses read access when this fixture reuses it.
    // Real OfflineDb metadata updates are covered separately in all engines.
    const blob = record.blob ? new Blob([await record.blob.arrayBuffer()], { type: record.blob.type }) : undefined
    const transaction = db.transaction(store, 'readwrite')
    transaction.objectStore(store).put({ ...record, state, ...(blob ? { blob } : {}) })
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve()
      transaction.onerror = transaction.onabort = () => reject(transaction.error)
    })
    db.close()
  }, { store, state })
}

async function expectWaitingAfterRequest(page: Page) {
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    registration?.waiting?.postMessage({ type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } })
  })
  await page.waitForTimeout(250)
  expect(await page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.waiting))).toBe(true)
}

for (const width of [390, 1440]) {
test(`installed-like production worker waits for action, media, and every client at ${width}px`, async ({ context, page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v1', legacyClient: false } })
  await page.goto('/')
  await waitForActiveWorker(page)
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v1')

  const privateUrls = [
    `/api/private-pwa-probe?nonce=${Date.now()}`,
    `/attachments/private-pwa-probe?nonce=${Date.now()}`,
  ]
  const onlineResponses = await page.evaluate(async (urls) => {
    return Promise.all(urls.map(async (url) => {
      const response = await fetch(url)
      return { status: response.status, body: await response.text() }
    }))
  }, privateUrls)
  expect(onlineResponses).toEqual([
    { status: 200, body: 'controlled-private-api-response' },
    { status: 200, body: 'controlled-private-attachment-response' },
  ])

  const cachedEntries = await page.evaluate(async () => {
    const names = await caches.keys()
    const entries = await Promise.all(names.map(async (name) => {
      const cache = await caches.open(name)
      const requests = await cache.keys()
      return Promise.all(requests.map(async (request) => ({
        cache: name,
        url: request.url,
        body: await cache.match(request).then((response) => response?.text() ?? ''),
      })))
    }))
    return entries.flat()
  })
  for (const entry of cachedEntries) {
    expect(entry.url).not.toContain('private-pwa-probe')
    expect(entry.body).not.toContain('controlled-private-')
  }

  await seedScopedOfflineRecords(page)
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v2' } })
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    await registration?.update()
  })
  await expect.poll(() => page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    return Boolean(registration?.waiting)
  })).toBe(true)
  await expectWaitingAfterRequest(page)
  await settleOfflineRecord(page, 'actions', 'confirmed')
  await expectWaitingAfterRequest(page)
  await settleOfflineRecord(page, 'media', 'confirmed')

  const delayedAuthTab = await context.newPage()
  await control(page, 'hold-auth', { hold: true })
  await delayedAuthTab.goto('/')
  await expectWaitingAfterRequest(page)
  await delayedAuthTab.close()
  await control(page, 'hold-auth', { hold: false })

  const reloaded = page.waitForEvent('load')
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    if (!registration?.waiting) throw new Error('waiting_worker_missing')
    sessionStorage.setItem('pwa-controller-change', 'waiting')
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      sessionStorage.setItem('pwa-controller-change', 'observed')
      window.location.reload()
    }, { once: true })
    registration.waiting.postMessage({ type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } })
  })
  await reloaded
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('pwa-controller-change'))).toBe('observed')
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v2')
  const updatedCaches = await page.evaluate(() => caches.keys())
  expect(updatedCaches.some(name => name.startsWith('robopark-shell-') && name.endsWith('-v2'))).toBe(true)
  expect(updatedCaches.some(name => name.endsWith('-v1'))).toBe(false)
  const updatedCacheUrls = await page.evaluate(async () => {
    const names = await caches.keys()
    return (await Promise.all(names.map(async name => (await (await caches.open(name)).keys()).map(request => request.url)))).flat()
  })
  expect(updatedCacheUrls.some(url => url.includes('private-pwa-probe'))).toBe(false)
  const records = await page.evaluate(async () => {
    const opening = indexedDB.open('robopark-offline')
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      opening.onsuccess = () => resolve(opening.result)
      opening.onerror = () => reject(opening.error)
    })
    const transaction = db.transaction(['actions', 'media', 'entities', 'meta', 'revisions'], 'readonly')
    const read = (store: string) => new Promise<unknown[]>((resolve, reject) => {
      const request = transaction.objectStore(store).getAll()
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => reject(request.error)
    })
    const [actions, media, entities, meta, revisions] = await Promise.all(['actions', 'media', 'entities', 'meta', 'revisions'].map(read))
    db.close()
    const preservedMedia = await Promise.all((media as { dbId: string, scope: string, state: string, blob: Blob }[]).map(async item => ({
      dbId: item.dbId, scope: item.scope, state: item.state, contents: await item.blob.text(),
    })))
    return { version: db.version, actions, media: preservedMedia, entities, meta, revisions }
  })
  expect(records).toEqual({
    version: 2,
      actions: [expect.objectContaining({ dbId: 'scope-a\0action', scope: 'scope-a', state: 'confirmed', payload: { text: 'preserved comment' } })],
    media: [{ dbId: 'scope-b\0photo', scope: 'scope-b', state: 'confirmed', contents: 'private-photo' }],
    entities: [{ dbId: 'scope-a\0draft', scope: 'scope-a', data: { title: 'preserved draft' } }],
    meta: [{ dbId: 'scope-b\0flag', scope: 'scope-b', value: 'preserved meta' }],
    revisions: [{ dbId: 'scope-a\0revision', scope: 'scope-a', revision: 'r1' }],
  })

  await setOffline(page, context, true)
  await page.goto('/login')
  const login = page.getByRole('textbox', { name: 'Логин' })
  await expect(login).toBeVisible()
  await expect(login).toBeEditable()
  await login.fill('offline-shell-proof')
  await expect(login).toHaveValue('offline-shell-proof')
  await expect(page.getByRole('button', { name: 'Войти', exact: true })).toBeEnabled()
  const privateResponses = await page.evaluate(async (urls) => Promise.all(urls.map(async (url) => {
    try {
      await fetch(url)
      return 'resolved'
    } catch {
      return 'rejected'
    }
  })), privateUrls)
  expect(privateResponses).toEqual(['rejected', 'rejected'])
  await setOffline(page, context, false)
  await page.evaluate(() => window.dispatchEvent(new Event('online')))
  await page.reload()
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v2')
  expect(await page.evaluate(async urls => Promise.all(urls.map(async url => {
    const response = await fetch(url)
    return { status: response.status, body: await response.text() }
  })), privateUrls)).toEqual(onlineResponses)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
}

test('a real old installed client keeps durable work and updates its open tab without a browser refresh', async ({ page }) => {
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v1', legacyClient: true } })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Установить обновление' })).toBeVisible()
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.active))).toBe(true)
  await page.reload()
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)
  await seedScopedOfflineRecords(page)
  expect(await page.evaluate(async () => {
    const opening = indexedDB.open('robopark-offline')
    const db = await new Promise<IDBDatabase>((resolve, reject) => { opening.onsuccess = () => resolve(opening.result); opening.onerror = () => reject(opening.error) })
    const transaction = db.transaction(['actions', 'media'])
    const read = (store: string) => new Promise<{ state: string }>((resolve, reject) => {
      const request = transaction.objectStore(store).getAll()
      request.onsuccess = () => resolve(request.result[0])
      request.onerror = () => reject(request.error)
    })
    const states = [(await read('actions')).state, (await read('media')).state]
    db.close()
    return states
  })).toEqual(['ready', 'local'])
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v2', legacyClient: false } })
  await page.evaluate(async () => (await navigator.serviceWorker.getRegistration('/'))?.update())
  await expect.poll(() => page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    return registration?.waiting ? 'waiting' : registration?.installing?.state ?? registration?.active?.state ?? 'none'
  })).toBe('waiting')
  await page.getByRole('button', { name: 'Установить обновление' }).click()
  await expect.poll(() => page.evaluate(() => (window as Window & { legacyActivationRequested?: boolean }).legacyActivationRequested)).toBe(true)
  await page.waitForTimeout(2200)
  expect(await page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.waiting))).toBe(true)
  await settleOfflineRecord(page, 'actions', 'confirmed')
  await settleOfflineRecord(page, 'media', 'confirmed')
  await page.getByRole('button', { name: 'Установить обновление' }).click()
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v2', { timeout: 12_000 })
  await expect(page.getByRole('textbox', { name: 'Логин' })).toBeVisible()
  await expect.poll(() => page.evaluate(async () => (await caches.keys()).some(name => name.endsWith('-v1')))).toBe(false)
})

test('a silent old installed client waits for its explicit update confirmation', async ({ page }) => {
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v1', legacyClient: true } })
  await page.goto('/')
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.active))).toBe(true)
  await page.reload()
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)

  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v2', legacyClient: false } })
  await page.evaluate(async () => (await navigator.serviceWorker.getRegistration('/'))?.update())
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.waiting))).toBe(true)
  await page.waitForTimeout(2200)
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v1')
  await page.getByRole('button', { name: 'Установить обновление' }).click()
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v2', { timeout: 12_000 })
  await expect(page.getByRole('textbox', { name: 'Логин' })).toBeVisible()
})

for (const width of [390, 1440]) test(`explicitly updated old PWA reaches mechanic work immediately after login at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  const user = userForRole('mechanic')
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v1', legacyClient: true } })
  await control(page, 'scenario', { role: 'mechanic', signedIn: false })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Установить обновление' })).toBeVisible()
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.active))).toBe(true)
  await page.reload()
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v2', legacyClient: false } })
  await page.evaluate(async () => (await navigator.serviceWorker.getRegistration('/'))?.update())
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.waiting))).toBe(true)
  await page.getByRole('button', { name: 'Установить обновление' }).click()
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v2', { timeout: 12_000 })
  await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: 'Логин' }).fill(user.username)
  await page.getByRole('textbox', { name: 'Пароль', exact: true }).fill(randomBytes(24).toString('base64url'))
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Работа', exact: true })).toBeVisible()
  await expect(page.locator('.rp-work-entities')).toBeVisible()
  await expect(page).toHaveURL(/\/work(?:\?|$)/)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `/tmp/robopark-pwa-old-worker-login-${width}.png` })
  await page.getByRole('button', { name: /Открыть задачу ROBOPARK-42/ }).click()
  await expect(page.getByRole('heading', { name: 'Проверить переднее левое колесо робота 447', exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'Проверка', exact: true }).click()
  await expect(page.getByText('АКБ 1', { exact: true })).toBeVisible()
  await expect(page.getByText('АКБ 2', { exact: true })).toBeVisible()
  await expect(page.getByText('АКБ 1 ниже 90%', { exact: true })).toBeVisible()
  await expect(page.getByText('АКБ 2 ниже 90%', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `/tmp/robopark-pwa-old-worker-check-${width}.png` })
})

for (const [role, landing] of [
  ['operator', '/overview'],
  ['driver', '/overview'],
  ['admin', '/admin'],
  ['royal', '/overview'],
] as const) test(`explicitly updated old PWA opens the ${role} landing after login`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  const user = userForRole(role)
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v1', legacyClient: true } })
  await control(page, 'scenario', { role, signedIn: false })
  await page.goto('/')
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.active))).toBe(true)
  await page.reload()
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v2', legacyClient: false } })
  await page.evaluate(async () => (await navigator.serviceWorker.getRegistration('/'))?.update())
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.waiting))).toBe(true)
  await page.getByRole('button', { name: 'Установить обновление' }).click()
  await expect(page.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v2', { timeout: 12_000 })
  await page.getByRole('textbox', { name: 'Логин' }).fill(user.username)
  await page.getByRole('textbox', { name: 'Пароль', exact: true }).fill(randomBytes(24).toString('base64url'))
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`${landing}(?:\\?|$)`))
  if (role === 'admin') {
    await expect(page.getByRole('heading', { name: 'Управление', exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Разделы управления' })).toBeVisible()
    await expect.poll(() => page.locator('.page-content.animate-in').evaluate(element => Number(getComputedStyle(element).opacity))).toBe(1)
  } else {
    await expect(page.getByRole('heading', { name: 'Что требует решения сейчас' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Статусы задач' }).first()).toBeVisible()
  }
  await expect(page.getByText('Загрузка...', { exact: true })).toHaveCount(0)
  if (role === 'operator' || role === 'royal') {
    await expect(page.locator('.rp-shell__park-brand-name')).toHaveText('Все парки')
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `/tmp/robopark-pwa-old-worker-login-${role}-390.png` })
})
