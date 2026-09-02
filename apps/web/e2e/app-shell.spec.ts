import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from './support/assertA11y'
import { installMockApi } from './support/mockApi'
import { mechanicUser, operatorUser } from './support/users'

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
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  const appOrigin = new URL(page.url()).origin
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).toBeVisible()
  expect(await page.evaluate(() => document.fonts.load('450 16px "Manrope Variable"', 'Робопарк'))).not.toHaveLength(0)
  expect(fontRequests).not.toHaveLength(0)
  expect(fontRequests.every((url) => new URL(url).origin === appOrigin)).toBe(true)
  expect(fontRequests.join('\n')).not.toMatch(/fonts\.(?:googleapis|gstatic)\.com/i)
  await waitForStableAudit(page)
  await assertNoSeriousA11yViolations(page)
  await expect(page).toHaveScreenshot('operator-shell-light-1440.png', { animations: 'disabled' })
})

test('mechanic shell keeps primary actions at 390px in dark theme', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'dark'))
  await installMockApi(page, { user: mechanicUser, parks: mechanicUser.parks })
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
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks })
  await page.goto('/robots?park=7')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByLabel('Номер робота или ключ тикета').fill('ROBOPARK-42')
  const timing = await page.evaluate(() => ({
    bootstrap: window.__roboparkThemeBootstrappedAt,
    firstPaint: performance.getEntriesByType('paint')[0]?.startTime ?? Number.POSITIVE_INFINITY,
  }))
  expect(timing.bootstrap).toBeLessThanOrEqual(timing.firstPaint)

  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page).toHaveURL(/\/robots\?park=7$/)
  await expect(page.getByLabel('Номер робота или ключ тикета')).toHaveValue('ROBOPARK-42')
})

test('desktop compact density becomes comfortable on phone and restores without losing context', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => {
    localStorage.setItem('robopark-theme', 'light')
    localStorage.setItem('robopark-density', 'compact')
  })
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks })
  await page.goto('/robots?park=7')
  await page.getByLabel('Номер робота или ключ тикета').fill('ROBOPARK-42')
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable')
  await expect(page.getByLabel('Номер робота или ключ тикета')).toHaveValue('ROBOPARK-42')
  await expect(page).toHaveURL(/\/robots\?park=7$/)

  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')
  expect(await page.evaluate(() => localStorage.getItem('robopark-density'))).toBe('compact')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})
