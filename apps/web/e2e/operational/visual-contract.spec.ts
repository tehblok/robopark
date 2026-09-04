import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, settlePage, snapshot } from './fixtures'

test('overview readiness follows the current Operations insights structure', async ({ page }) => {
  await installOperational(page, { role: 'operator' })
  await page.goto('/overview?park=7')

  await expect(page.locator('.rp-insights')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Текущие задачи' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Просрочки SLA' })).toBeVisible()
  await expect(page.locator('.rp-overview-primary')).toHaveCount(0)
})

test('light-theme related robot task link meets the WCAG AA contract', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'light'))
  await installOperational(page, { role: 'operator' })
  await page.goto(`/robots/${snapshot.vin}?park=7`)

  await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
  await settlePage(page)
  await assertNoSeriousA11yViolations(page)
})
