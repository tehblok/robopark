import { expect, test } from '@playwright/test'
import { assertResponsiveContracts, assertRouteSemanticContracts, geometryRouteIdsFor, openRouteFixture } from './routeFixtures'
import { userForRole, settlePage } from './fixtures'

const geometryRoles = ['mechanic', 'operator', 'admin', 'royal'] as const
const geometryThemes = ['light', 'dark'] as const
const geometryViewports = [
  { name: 'phone', width: 390, height: 844 },
  { name: 'desktop', width: 1440, height: 1000 },
] as const

test('schedule route fixture reaches the successful schedule workspace', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'schedule', userForRole('mechanic'))
  await expect(page.locator('.rp-schedule__list[data-view="week"]')).toBeVisible()
  await expect(page.getByText('Не удалось загрузить график', { exact: true })).toHaveCount(0)
  await assertResponsiveContracts(page, 390)
})

test('geometry helper audits descendant text against its nearest bordered container', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 800 })
  await page.setContent(`
    <style>* { box-sizing: border-box; font: 16px sans-serif } .outer { border: 1px solid; padding: 0 } .own { border: 1px solid; display: inline-flex; padding: 8px }</style>
    <div class="outer"><span>Too close</span></div>
    <div class="outer"><span class="own">Own border is padded</span></div>
  `)
  let failure = ''
  try { await assertResponsiveContracts(page, 1000) } catch (error) { failure = String(error) }
  expect(failure).toMatch(/text inset .*Too close/)
  expect(failure).not.toContain('Own border is padded')
})

test('geometry helper checks overlay controls against flow controls', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 800 })
  await page.setContent(`
    <style>* { box-sizing: border-box; font: 16px sans-serif } button { min-width: 80px; min-height: 44px } .overlay { position: fixed; inset: 0 auto auto 0 }</style>
    <button>Flow action</button><button class="overlay">Overlay action</button>
  `)
  await expect(assertResponsiveContracts(page, 1000)).rejects.toThrow(/overlap .*Flow action.*Overlay action|overlap .*Overlay action.*Flow action/)
})

test('geometry helper includes summary and inline link hit areas', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 800 })
  await page.setContent(`
    <style>* { box-sizing: border-box; font: 16px sans-serif } details { margin-bottom: 20px } summary, a { font-size: 16px }</style>
    <details><summary>Compact disclosure</summary><p>Content</p></details>
    <p><a href="#target">Compact inline link</a></p>
  `)
  await expect(assertResponsiveContracts(page, 1000)).rejects.toThrow(/target .*Compact disclosure/)
  await page.locator('summary').evaluate(element => { element.style.minHeight = '44px'; element.style.display = 'flex' })
  await expect(assertResponsiveContracts(page, 1000)).rejects.toThrow(/target .*Compact inline link/)
})

test('production disclosure keeps its native marker and 44px hit area', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'analytics', userForRole('operator'))
  const summary = page.locator('.rp-analytics-card summary', { hasText: 'Значения по интервалам' }).first()
  await expect(summary).toBeVisible()
  const geometry = await summary.evaluate(element => {
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    return { display: style.display, listStyleType: style.listStyleType, height: rect.height }
  })
  expect(geometry.display).toBe('list-item')
  expect(geometry.listStyleType).not.toBe('none')
  expect(geometry.height).toBeGreaterThanOrEqual(44)
})

for (const role of geometryRoles) {
  const user = userForRole(role)
  for (const theme of geometryThemes) for (const viewport of geometryViewports) {
    test(`${role} routes satisfy ${theme} ${viewport.name} geometry`, async ({ page }) => {
      for (const route of geometryRouteIdsFor(user)) await test.step(route, async () => {
        await page.setViewportSize(viewport)
        await page.addInitScript(themeName => localStorage.setItem('robopark-theme', themeName), theme)
        await openRouteFixture(page, route, user)
        await settlePage(page)
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
        await assertRouteSemanticContracts(page, route)
        await assertResponsiveContracts(page, viewport.width)
      })
    })
  }
}

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
