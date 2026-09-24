import { expect, test, type Page } from '@playwright/test'

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
      opening.onerror = () => reject(opening.error)
    })
    const transaction = db.transaction(['actions', 'media', 'entities', 'meta', 'revisions'], 'readwrite')
    transaction.objectStore('actions').put({ dbId: 'scope-a\0action', scope: 'scope-a', state: 'ready' })
    transaction.objectStore('media').put({ dbId: 'scope-b\0photo', scope: 'scope-b', state: 'local', blob: new Blob(['private-photo']) })
    transaction.objectStore('entities').put({ dbId: 'scope-a\0draft', scope: 'scope-a', data: { title: 'preserved draft' } })
    transaction.objectStore('meta').put({ dbId: 'scope-b\0flag', scope: 'scope-b', value: 'preserved meta' })
    transaction.objectStore('revisions').put({ dbId: 'scope-a\0revision', scope: 'scope-a', revision: 'r1' })
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
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
    const transaction = db.transaction(store, 'readwrite')
    const key = store === 'actions' ? 'scope-a\0action' : 'scope-b\0photo'
    const request = transaction.objectStore(store).get(key)
    request.onsuccess = () => transaction.objectStore(store).put({ ...request.result, state })
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve()
      transaction.onerror = () => reject(transaction.error)
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
  await expect.poll(() => page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    return Boolean(registration?.active)
  })).toBe(true)

  await page.reload()
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)
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

  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v2' } })
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    await registration?.update()
  })
  await expect.poll(() => page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    return Boolean(registration?.waiting)
  })).toBe(true)
  await seedScopedOfflineRecords(page)
  await expectWaitingAfterRequest(page)
  await settleOfflineRecord(page, 'actions', 'confirmed')
  await expectWaitingAfterRequest(page)
  await settleOfflineRecord(page, 'media', 'confirmed')

  const delayedAuthTab = await context.newPage()
  await delayedAuthTab.route('**/api/auth/me', () => new Promise(() => {}))
  await delayedAuthTab.goto('/')
  await expectWaitingAfterRequest(page)
  await delayedAuthTab.close()

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
    return { version: db.version, actions, media: (media as { dbId: string, scope: string, state: string }[]).map(item => ({ dbId: item.dbId, scope: item.scope, state: item.state })), entities, meta, revisions }
  })
  expect(records).toEqual({
    version: 2,
    actions: [{ dbId: 'scope-a\0action', scope: 'scope-a', state: 'confirmed' }],
    media: [{ dbId: 'scope-b\0photo', scope: 'scope-b', state: 'confirmed' }],
    entities: [{ dbId: 'scope-a\0draft', scope: 'scope-a', data: { title: 'preserved draft' } }],
    meta: [{ dbId: 'scope-b\0flag', scope: 'scope-b', value: 'preserved meta' }],
    revisions: [{ dbId: 'scope-a\0revision', scope: 'scope-a', revision: 'r1' }],
  })

  await context.setOffline(true)
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
  await context.setOffline(false)
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

test('a real old client sends the state-less protocol and waits until its tab closes', async ({ context, page }) => {
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v1', legacyClient: true } })
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Установить обновление' })).toBeVisible()
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.active))).toBe(true)
  await page.reload()
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)
  await page.request.post('/__pwa_fixture__/version', { data: { version: 'v2', legacyClient: false } })
  await page.evaluate(async () => (await navigator.serviceWorker.getRegistration('/'))?.update())
  await expect.poll(() => page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.waiting))).toBe(true)
  await page.getByRole('button', { name: 'Установить обновление' }).click()
  await expect.poll(() => page.evaluate(() => (window as Window & { legacyActivationRequested?: boolean }).legacyActivationRequested)).toBe(true)
  await page.waitForTimeout(2200)
  expect(await page.evaluate(async () => Boolean((await navigator.serviceWorker.getRegistration('/'))?.waiting))).toBe(true)
  await page.close()
  await new Promise(resolve => setTimeout(resolve, 150))
  const updated = await context.newPage()
  await updated.goto('/')
  await expect.poll(() => updated.evaluate(async () => (await caches.keys()).some(name => name.endsWith('-v1')))).toBe(false)
  await updated.reload()
  await expect(updated.locator('meta[name="pwa-fixture-version"]')).toHaveAttribute('content', 'v2')
})
