import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from './support/assertA11y'
import { installMockApi, type MockRoute } from './support/mockApi'
import { mechanicUser, northPark, operatorUser } from './support/users'

const currentOverviewRoutes: MockRoute[] = [{
  method: 'GET',
  path: '/api/dashboard/summary',
  handler: request => ({ json: {
    park_id: Number(new URL(request.url).searchParams.get('park_id')),
    generated_at: '2026-09-02T09:00:00Z',
    arrived: 0, done: 0, queued: 0, in_transit: 0, moving: [],
  } }),
}]

test.beforeEach(async ({ page }) => {
  // A fixed Date keeps the five-minute freshness boundary deterministic while
  // ordinary timers and animations remain available to the shell.
  await page.clock.setFixedTime(new Date('2026-09-02T09:05:00Z'))
})

test('browser tab uses the local robot icon', async ({ page }) => {
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks, routes: currentOverviewRoutes })
  await page.goto('/')
  const icon = page.locator('link[rel="icon"]')
  await expect(icon).toHaveAttribute('href', '/favicon.svg')
  const response = await page.request.get('/favicon.svg')
  expect(response.status()).toBe(200)
  expect(await response.text()).toContain('<svg')
})

test('PWA manifest installs the same product icon without requiring user data', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/manifest.webmanifest')
  const response = await page.request.get('/manifest.webmanifest')
  expect(response.ok()).toBe(true)
  const manifest = await response.json() as {
    name: string; short_name: string; start_url: string; scope: string; display: string
    icons: { src: string; sizes: string; type: string }[]
  }
  expect(manifest).toMatchObject({
    name: 'Работа', short_name: 'Работа',
    start_url: '/', scope: '/', display: 'standalone',
  })
  for (const size of [192, 512]) {
    const icon = manifest.icons.find(item => item.sizes === `${size}x${size}`)
    expect(icon?.type).toBe('image/png')
    const iconResponse = await page.request.get(icon!.src)
    expect(iconResponse.ok()).toBe(true)
    const png = await iconResponse.body()
    expect(png.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a')
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([size, size])
  }
  const offline = await page.request.get('/offline.html')
  expect(offline.ok()).toBe(true)
  expect(await offline.text()).toContain('Нет соединения')
})

async function waitForStableAudit(page: import('@playwright/test').Page) {
  await expect(page.locator('.global-progress')).toHaveAttribute('aria-hidden', 'true')
  await page.evaluate(async () => {
    const finiteAnimations = document.getAnimations().filter((animation) => (
      animation.effect?.getComputedTiming().iterations !== Number.POSITIVE_INFINITY
    ))
    await Promise.allSettled(finiteAnimations.map((animation) => animation.finished))
  })
}

test('operator shell is accessible in the light theme', async ({ page }) => {
  const fontRequests: string[] = []
  page.on('request', (request) => {
    if (request.resourceType() === 'font') fontRequests.push(request.url())
  })
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'light'))
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks, routes: currentOverviewRoutes })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  const appOrigin = new URL(page.url()).origin
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).toBeVisible()
  expect(await page.evaluate(() => document.fonts.load('450 16px "Manrope Variable"', 'РобоПарк'))).not.toHaveLength(0)
  expect(fontRequests).not.toHaveLength(0)
  expect(fontRequests.every((url) => new URL(url).origin === appOrigin)).toBe(true)
  expect(fontRequests.join('\n')).not.toMatch(/fonts\.(?:googleapis|gstatic)\.com/i)
  await waitForStableAudit(page)
  await assertNoSeriousA11yViolations(page)
  await expect(page).toHaveScreenshot('operator-shell-light-1440.png', { animations: 'disabled' })
})

for (const width of [320, 1440]) {
  test(`operator park switcher exposes its accessible listbox at ${width}px`, async ({ page }) => {
    const southPark = { ...northPark, id: 8, name: 'Next' }
    const user = { ...operatorUser, parks: [northPark, southPark] }
    await installMockApi(page, { user, parks: user.parks, routes: currentOverviewRoutes })
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/overview?park=7')

    const switcher = page.getByRole('button', { name: 'Сменить парк' })
    await expect(switcher).toContainText('Северный парк')
    const switcherBox = await switcher.boundingBox()
    expect(switcherBox).not.toBeNull()
    expect(switcherBox!.height).toBeGreaterThanOrEqual(44)

    await switcher.click()
    await expect(page.getByRole('listbox', { name: 'Сменить парк' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(switcher).toBeFocused()
    await switcher.click()
    await page.getByRole('option', { name: 'Next' }).click()
    await expect(page).toHaveURL(/\/overview\?park=8$/)
  })
}

test('mechanic shell keeps primary actions at 390px in dark theme', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'dark'))
  await installMockApi(page, { user: mechanicUser, parks: mechanicUser.parks, routes: currentOverviewRoutes })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/overview')
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).toBeVisible()
  await waitForStableAudit(page)
  await assertNoSeriousA11yViolations(page)
  await expect(page).toHaveScreenshot('mechanic-shell-dark-390.png', { animations: 'disabled' })
})

test('system theme is applied before paint and follows live OS changes without losing context', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'system'))
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks, routes: currentOverviewRoutes })
  await page.goto('/robots?park=7')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByLabel('Номер или VIN робота').fill('447')
  const timing = await page.evaluate(() => ({
    bootstrap: window.__roboparkThemeBootstrappedAt,
    firstPaint: performance.getEntriesByType('paint')[0]?.startTime ?? Number.POSITIVE_INFINITY,
  }))
  expect(timing.bootstrap).toBeLessThanOrEqual(timing.firstPaint)

  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page).toHaveURL(/\/robots\?park=7&q=447$/)
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
})

test('compact density keeps touch targets and search context on phone and desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => {
    localStorage.setItem('robopark-theme', 'light')
    localStorage.setItem('robopark-density', 'compact')
  })
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks, routes: currentOverviewRoutes })
  await page.goto('/robots?park=7')
  await page.getByLabel('Номер или VIN робота').fill('447')
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')
  const searchBox = await page.getByLabel('Номер или VIN робота').boundingBox()
  expect(searchBox!.height).toBeGreaterThanOrEqual(44)
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await expect(page).toHaveURL(/\/robots\?park=7&q=447$/)

  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')
  expect(await page.evaluate(() => localStorage.getItem('robopark-density'))).toBe('compact')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await expect(page).toHaveURL(/\/robots\?park=7&q=447$/)
})
