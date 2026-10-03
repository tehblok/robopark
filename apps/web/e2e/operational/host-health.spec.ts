import { expect, test } from '@playwright/test'
import { installOperational, settlePage } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

for (const width of [320, 1440]) test(`server status remains readable at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 })
  await installOperational(page, { role: 'admin', routes: [{ method: 'GET', path: '/api/admin/health', handler: () => ({ json: {
    sampled_at: 1788688800, database: 'ok', window_seconds: 300,
    disk: { total_bytes: 64 * 1024 ** 3, free_bytes: 1024 ** 3 },
    memory: { total_bytes: 8 * 1024 ** 3, available_bytes: 3 * 1024 ** 3, container_used_bytes: 350 * 1024 ** 2, container_limit_bytes: 3 * 1024 ** 3 },
    backup: { verified_at: 1788508800, overdue: true, last_attempt_failed: true },
    requests: { tracker: { requests: 320, errors: 2, limited: 1, average_ms: 120, max_ms: 2700 }, diagnostics: { requests: 600, errors: 0, limited: 0, average_ms: 80, max_ms: 200 }, reports: { requests: 0, errors: 0, limited: 0, average_ms: null, max_ms: null } },
  } }) }] })
  await page.goto('/admin/settings?park=7&tab=health')
  await expect(page.getByRole('heading', { name: 'Сервер', exact: true })).toBeVisible()
  await expect(page.getByText(/Мало свободного места/)).toBeVisible()
  await expect(page.getByText(/Более 36 часов/)).toBeVisible()
  await settlePage(page)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await assertNoSeriousA11yViolations(page)
  await page.screenshot({ path: info.outputPath(`server-${width}.png`), fullPage: true })
})

test('server status names missing host readings on a narrow phone', async ({ page }, info) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await installOperational(page, { role: 'admin', routes: [{ method: 'GET', path: '/api/admin/health', handler: () => ({ json: {
    sampled_at: 1788688800, database: 'ok', window_seconds: 300,
    host_health_source_state: 'unavailable',
    disk: { total_bytes: null, free_bytes: null, source_state: 'unavailable' },
    memory: { total_bytes: null, available_bytes: null, container_used_bytes: null, container_limit_bytes: null },
    backup: { verified_at: null, overdue: false, last_attempt_failed: false },
    requests: {},
  } }) }] })
  await page.goto('/admin/settings?park=7&tab=health')
  await expect(page.getByText(/Не удалось измерить диск API/)).toBeVisible()
  await expect(page.getByText(/Снимок host agent отсутствует/)).toBeVisible()
  await settlePage(page)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await assertNoSeriousA11yViolations(page)
  await page.screenshot({ path: info.outputPath('server-missing-host-320.png'), fullPage: true })
})
