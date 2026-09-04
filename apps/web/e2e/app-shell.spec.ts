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

async function waitForStableAudit(page: import('@playwright/test').Page) {
  await expect(page.locator('.global-progress')).toHaveAttribute('aria-hidden', 'true')
  await page.evaluate(async () => {
    const finiteAnimations = document.getAnimations().filter((animation) => (
      animation.effect?.getComputedTiming().iterations !== Number.POSITIVE_INFINITY
    ))
    await Promise.all(finiteAnimations.map((animation) => animation.finished))
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
  test(`operator park wordmark is the switch target at ${width}px`, async ({ page }) => {
    const southPark = { ...northPark, id: 8, name: 'Next' }
    const user = { ...operatorUser, parks: [northPark, southPark] }
    await installMockApi(page, { user, parks: user.parks, routes: currentOverviewRoutes })
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/overview?park=7')

    const wordmark = page.locator('.rp-shell__park-brand')
    const switcher = page.getByRole('combobox', { name: 'Сменить парк' })
    const [wordmarkBox, switcherBox] = await Promise.all([wordmark.boundingBox(), switcher.boundingBox()])
    expect(wordmarkBox).not.toBeNull()
    expect(switcherBox).not.toBeNull()
    expect(Math.abs(switcherBox!.x - wordmarkBox!.x)).toBeLessThanOrEqual(1)
    expect(Math.abs(switcherBox!.y - wordmarkBox!.y)).toBeLessThanOrEqual(1)
    expect(Math.abs(switcherBox!.width - wordmarkBox!.width)).toBeLessThanOrEqual(2)
    expect(switcherBox!.height).toBe(wordmarkBox!.height)
    expect(switcherBox!.height).toBeGreaterThanOrEqual(44)

    await switcher.evaluate((element) => element.addEventListener('click', () => {
      document.body.dataset.parkSwitcherClicked = 'true'
    }, { once: true }))
    await wordmark.click()
    await expect(page.locator('body')).toHaveAttribute('data-park-switcher-clicked', 'true')
    await page.keyboard.press('Escape')
    await switcher.selectOption('8')
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

test('desktop compact density becomes comfortable on phone and restores without losing context', async ({ page }) => {
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
  await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable')
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await expect(page).toHaveURL(/\/robots\?park=7&q=447$/)

  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')
  expect(await page.evaluate(() => localStorage.getItem('robopark-density'))).toBe('compact')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await expect(page).toHaveURL(/\/robots\?park=7&q=447$/)
})
