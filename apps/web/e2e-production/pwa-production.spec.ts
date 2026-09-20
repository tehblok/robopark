import { expect, test } from '@playwright/test'

test('production worker safely activates a waiting update with an empty offline queue', async ({ context, page }) => {
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
  const offlineActions = await page.evaluate(async () => {
    const databases = await indexedDB.databases()
    if (!databases.some(database => database.name === 'robopark-offline')) return 0
    return new Promise<number>((resolve, reject) => {
      const request = indexedDB.open('robopark-offline')
      request.onerror = () => reject(request.error)
      request.onsuccess = () => {
        const db = request.result
        const count = db.transaction('actions', 'readonly').objectStore('actions').count()
        count.onerror = () => reject(count.error)
        count.onsuccess = () => { db.close(); resolve(count.result) }
      }
    })
  })
  expect(offlineActions).toBe(0)

  const reloaded = page.waitForEvent('load')
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    if (!registration?.waiting) throw new Error('waiting_worker_missing')
    sessionStorage.setItem('pwa-controller-change', 'waiting')
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      sessionStorage.setItem('pwa-controller-change', 'observed')
      window.location.reload()
    }, { once: true })
    registration.waiting.postMessage({ type: 'ACTIVATE_WHEN_SAFE' })
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
})
