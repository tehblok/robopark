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
  await page.evaluate(async (urls) => {
    await Promise.all(urls.map((url) => fetch(url).then((response) => response.text())))
  }, privateUrls)

  const cachedUrls = await page.evaluate(async () => {
    const names = await caches.keys()
    const requests = await Promise.all(names.map(async (name) => (await caches.open(name)).keys()))
    return requests.flat().map((request) => request.url)
  })
  for (const url of privateUrls) expect(cachedUrls.some((cached) => cached.includes(url))).toBe(false)

  await context.setOffline(true)
  await page.goto('/work/offline-pwa-proof')
  await expect(page.locator('#root')).toBeAttached()
  await expect(page).toHaveTitle(/\S+/)
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
