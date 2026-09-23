import { expect, test } from '@playwright/test'
import { assertResponsiveContracts, openRouteFixture } from './routeFixtures'
import { userForRole, settlePage } from './fixtures'

for (const mode of ['Классический'] as const) test(`${mode} separates management summary from the next panel`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await settlePage(page)

  const metrics = await page.locator('.rp-management-metrics').boundingBox()
  const panel = await page.locator('.rp-management-metrics + .panel').boundingBox()
  expect(metrics).not.toBeNull()
  expect(panel).not.toBeNull()
  expect(panel!.y - (metrics!.y + metrics!.height)).toBeGreaterThanOrEqual(16)
})

for (const mode of ['Классический'] as const) test(`${mode} keeps park selection compact and centers automatic sync status`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await settlePage(page)

  const park = await page.locator('.rp-shell__topbar .rp-shell__park-brand').boundingBox()
  const indicator = await page.locator('.rp-sync-center__trigger').boundingBox()
  const dot = await page.locator('.rp-sync-center__dot').boundingBox()
  expect(park).not.toBeNull()
  expect(indicator).not.toBeNull()
  expect(dot).not.toBeNull()
  expect(park!.width).toBeLessThan(220)
  expect(Math.abs(dot!.x + dot!.width / 2 - (indicator!.x + indicator!.width / 2))).toBeLessThanOrEqual(2)
})

for (const mode of ['Классический'] as const) test(`${mode} separates task and related-work navigation on narrow screens`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  const mainTabs = await page.getByRole('tablist', { name: 'Разделы задачи' }).boundingBox()
  const relatedTabs = await page.getByRole('tablist', { name: 'Другие задачи робота' }).boundingBox()
  expect(mainTabs).not.toBeNull()
  expect(relatedTabs).not.toBeNull()
  expect(relatedTabs!.y - (mainTabs!.y + mainTabs!.height)).toBeGreaterThanOrEqual(12)
})

for (const mode of ['Классический'] as const) test(`${mode} keeps the task-management action compact on desktop`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'work-issue', userForRole('royal'))
  await settlePage(page)
  const control = page.getByRole('button', { name: 'Скрыть задачу' })
  await expect(control).toBeVisible()
  const button = await control.boundingBox()
  const section = await page.getByRole('region', { name: 'Управление задачей' }).boundingBox()
  expect(button).not.toBeNull()
  expect(section).not.toBeNull()
  expect(button!.width).toBeLessThan(section!.width / 2)
})

test('Classic shows roles across the workspace until a role is selected', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await page.goto('/admin/roles?park=7')
  await settlePage(page)
  const layout = page.locator('.admin-roles .rp-master-detail')
  await expect(layout).toHaveAttribute('data-detail-empty', 'true')
  await expect(layout.locator('.rp-master-detail__detail')).toHaveCount(0)
  const list = await layout.locator('.rp-master-detail__list').boundingBox()
  expect(list!.width).toBeGreaterThan(900)
  await page.getByRole('button', { name: /Открыть роль/ }).first().click()
  await expect(layout).toHaveAttribute('data-detail-empty', 'false')
  await expect(layout.locator('.rp-master-detail__detail')).toBeVisible()
})

for (const mode of ['Классический'] as const) test(`${mode} separates server health panels`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin-settings', userForRole('royal'), { routes: [{
    method: 'GET', path: '/api/admin/health', handler: () => ({ json: {
      sampled_at: 1789980000, database: 'ok', window_seconds: 300,
      disk: { total_bytes: 10000000000, free_bytes: 7000000000 },
      memory: { total_bytes: 8000000000, available_bytes: 5000000000, container_limit_bytes: 2000000000, container_used_bytes: 300000000 },
      backup: { verified_at: null, overdue: false, last_attempt_failed: false }, requests: {},
    } }),
  }] })
  await page.goto('/admin/settings?park=7&tab=health')
  const first = await page.locator('.stack > .panel').nth(0).boundingBox()
  const second = await page.locator('.stack > .panel').nth(1).boundingBox()
  expect(first).not.toBeNull()
  expect(second).not.toBeNull()
  expect(second!.y - (first!.y + first!.height)).toBeGreaterThanOrEqual(16)
})

test('appearance choices use compact radio circles with full-size clickable rows', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin', userForRole('royal'))
  await page.getByRole('button', { name: 'Ещё', exact: true }).click()
  const input = await page.locator('.rp-shell__preference-group input[type="radio"]').first().boundingBox()
  const row = await page.locator('.rp-shell__preference-group label').first().boundingBox()
  expect(input).not.toBeNull()
  expect(row).not.toBeNull()
  expect(input!.width).toBeLessThanOrEqual(24)
  expect(row!.height).toBeGreaterThanOrEqual(44)
})

for (const mode of ['Классический'] as const) test(`${mode} separates robot-check search and result card`, async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
  await settlePage(page)
  const search = await page.getByRole('searchbox', { name: 'Поиск разделов' }).boundingBox()
  const list = await page.locator('.card-list').first().boundingBox()
  expect(search).not.toBeNull()
  expect(list).not.toBeNull()
  expect(list!.y - (search!.y + search!.height)).toBeGreaterThanOrEqual(12)
})

for (const route of ['admin', 'admin-roles', 'admin-robot-check', 'reports', 'campaigns', 'work-issue'] as const) {
  for (const mode of ['Классический'] as const) test(`${mode} ${route} keeps a usable desktop width`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.addInitScript(() => localStorage.setItem('robopark-theme', 'dark'))
    await openRouteFixture(page, route, userForRole(route === 'work-issue' ? 'mechanic' : 'royal'))
    await settlePage(page)
    await assertResponsiveContracts(page, 1440)
  })
}

for (const route of ['admin-roles', 'admin-robot-check', 'reports', 'work-issue'] as const) {
  for (const mode of ['Классический'] as const) test(`${mode} ${route} fits a narrow light viewport`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.addInitScript(() => localStorage.setItem('robopark-theme', 'light'))
    await openRouteFixture(page, route, userForRole(route === 'work-issue' ? 'mechanic' : 'royal'))
    await settlePage(page)
    await assertResponsiveContracts(page, 390)
  })
}
