import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, settlePage, snapshot } from './fixtures'

test('overview readiness follows the current role-aware triage structure', async ({ page }) => {
  await installOperational(page, { role: 'operator' })
  await page.goto('/overview?park=7')

  await expect(page.locator('.rp-overview')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Статусы задач' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Поток задач: пришло / ушло' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Очередь внимания' })).toBeVisible()
  await expect(page.locator('.rp-insights')).toHaveCount(0)
})

test('light-theme related robot task link meets the WCAG AA contract', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'light'))
  await installOperational(page, { role: 'operator' })
  await page.goto(`/robots/${snapshot.vin}?park=7`)

  await page.getByRole('tab', { name: 'Задачи' }).click()
  await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
  await settlePage(page)
  await assertNoSeriousA11yViolations(page)
})
