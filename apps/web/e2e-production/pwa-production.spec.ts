import { expect, test } from '@playwright/test'

test('production worker owns the shell offline without caching private traffic', async ({ context, page }) => {
  await page.goto('/')
  await expect.poll(() => page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration('/')
    return Boolean(registration?.active)
  })).toBe(true)

  await page.reload()
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true)

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
