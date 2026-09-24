import { expect, test } from '@playwright/test'
import { canAccessRoute } from '../../src/app/routing/accessPolicy'
import { ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'
import type { User } from '../../src/api'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { parkNorth, roles, userForRole } from './fixtures'
import { assertResponsiveContracts, assertRouteSemanticContracts, openRouteFixture } from './routeFixtures'

const widths = [360, 390, 412, 768, 1024, 1366, 1440, 1920] as const
const themes = ['light', 'dark', 'system'] as const
const densities = ['compact', 'comfortable'] as const
test.describe.configure({ mode: 'parallel' })
const restrictedUser: User = {
  id: 160,
  username: 'field-lead-e2e',
  role: 'field_lead',
  access_status: 'approved',
  tracker_login: 'field.lead',
  must_change_password: false,
  screenshot_guard: false,
  permissions: ['nav.dashboard', 'nav.robot_search', 'nav.reports', 'reports.create', 'tracker.read'],
  parks: [parkNorth],
}

for (const mode of ['Классический'] as const) for (const role of roles) for (const route of ROUTE_MANIFEST.filter(item => item.surface === 'shell')) for (const width of widths) for (const theme of themes) for (const density of densities) {
  test(`${mode} ${role}: ${route.id} at ${width}px ${theme} ${density}`, async ({ page }) => {
    const user = userForRole(role)
    test.skip(!canAccessRoute(user, route.id), 'route denied by access policy')
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ colorScheme: theme === 'system' ? 'dark' : theme })
    await page.addInitScript(({ theme, density }) => {
      localStorage.setItem('robopark-theme', theme)
      localStorage.setItem('robopark-density', density)
    }, { theme, density })
    await openRouteFixture(page, route.id, user)
    await expect(page.locator('html')).toHaveAttribute('data-density', density)
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme === 'system' ? 'dark' : theme)
    await assertResponsiveContracts(page, width)
    await assertRouteSemanticContracts(page, route.id)
    await assertNoSeriousA11yViolations(page)
  })
}

for (const mode of ['Классический'] as const) for (const route of ROUTE_MANIFEST.filter(item => item.surface === 'shell')) for (const width of [390, 1440] as const) {
  test(`${mode} restricted: ${route.id} at ${width}px`, async ({ page }) => {
    test.skip(!canAccessRoute(restrictedUser, route.id), 'route denied by access policy')
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, route.id, restrictedUser)
    await assertResponsiveContracts(page, width)
    await assertRouteSemanticContracts(page, route.id)
    await assertNoSeriousA11yViolations(page)
  })
}

for (const mode of ['Классический'] as const) {
  test(`${mode} restricted role cannot retain protected administration`, async ({ page }) => {
    await openRouteFixture(page, 'overview', restrictedUser)
    await page.goto('/admin/users?park=7')
    await expect(page).not.toHaveURL(/\/admin\/users/)
    await expect(page.getByRole('button', { name: /Открыть аккаунт/ })).toHaveCount(0)
  })
}

test('operator overview exposes the loaded secondary summary before its strict layout contract', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await openRouteFixture(page, 'overview', userForRole('operator'))
  await expect(page.getByText('Поток, нагрузка и учётные записи', { exact: true })).toBeVisible()
  await assertResponsiveContracts(page, 320)
})

test('route fixtures prove loaded operator, campaign, report-detail and administration workflows', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  let releaseParks!: () => void
  let releaseAvailableParks!: () => void
  const parksGate = new Promise<void>((resolve) => { releaseParks = resolve })
  const availableParksGate = new Promise<void>((resolve) => { releaseAvailableParks = resolve })
  const parksReady = openRouteFixture(page, 'operator-parks', userForRole('operator'), {
    routes: [
      { method: 'GET', path: '/api/operator/parks', handler: async () => {
        await parksGate
        return { json: [parkNorth] }
      } },
      { method: 'GET', path: '/api/operator/available-parks', handler: async () => {
        await availableParksGate
        return { json: [{ ...parkNorth, id: 8, name: 'Южный парк', tag: 'south' }] }
      } },
    ],
  })
  await expect(page.getByRole('heading', { name: 'Мои парки', exact: true, level: 1 })).toBeVisible()
  expect(await Promise.race([parksReady.then(() => true), new Promise<false>((resolve) => setTimeout(() => resolve(false), 250))])).toBe(false)
  releaseParks()
  await expect(page.locator('.park-card-title', { hasText: 'Северный парк' })).toBeVisible()
  await expect(page.getByText('Парк #8', { exact: true })).toBeVisible()
  expect(await Promise.race([parksReady.then(() => true), new Promise<false>((resolve) => setTimeout(() => resolve(false), 1000))])).toBe(false)
  releaseAvailableParks()
  await parksReady
  const availableParkPanel = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'Запросить парк', exact: true, level: 2 }) })
  await availableParkPanel.getByRole('button', { name: 'Запросить парк', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Запросить парк' }).getByLabel('Парк', { exact: true })).toHaveValue('8')
  await expect(page.getByText('operator-e2e', { exact: true })).toBeVisible()

  await openRouteFixture(page, 'campaigns', userForRole('mechanic'))
  await expect(page.getByRole('heading', { name: 'Осенняя сервисная кампания', exact: true })).toBeVisible()
  await openRouteFixture(page, 'campaign-detail', userForRole('mechanic'))
  await expect(page.getByText('ROBOPARK-42', { exact: true })).toBeVisible()

  await openRouteFixture(page, 'report-detail', userForRole('mechanic'))
  await expect(page.getByText('Робот требует осмотра.', { exact: true })).toBeVisible()

  for (const routeId of ['admin-settings', 'admin-users', 'admin-roles', 'admin-tracker', 'admin-robot-check'] as const) {
    await openRouteFixture(page, routeId, userForRole('royal'))
    if (routeId === 'admin-tracker') {
      await expect(page).toHaveURL(/\/work\?/)
      await expect(page.getByRole('heading', { name: 'Рабочий стол Startrek', exact: true })).toHaveCount(0)
    }
    const marker = routeId === 'admin-settings'
      ? page.getByText('Tracker OAuth', { exact: true })
      : routeId === 'admin-users'
          ? page.getByRole('button', { name: 'Открыть аккаунт route-admin', exact: true })
        : routeId === 'admin-roles'
          ? page.getByText('Механик', { exact: true })
          : routeId === 'admin-tracker'
            ? page.getByRole('heading', { name: 'Очередь задач', exact: true })
          : page.getByRole('button', { name: 'Открыть раздел Колёса', exact: true })
    await expect(marker).toBeVisible()
  }
})

test('inventory is loaded for every production-authorized role and denied to driver', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  for (const role of ['mechanic', 'operator', 'admin', 'royal'] as const) {
    const user = userForRole(role)
    expect(canAccessRoute(user, 'inventory')).toBe(true)
    await openRouteFixture(page, 'inventory', user)
    await expect(page.getByText('ABC-1', { exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
  }
  const driver = userForRole('driver')
  expect(canAccessRoute(driver, 'inventory')).toBe(false)
  await openRouteFixture(page, 'overview', driver)
  await page.goto('/inventory?park=7')
  await expect(page).toHaveURL(/\/overview(?:\?|$)/)
  await expect(page.getByRole('heading', { name: 'Склад', exact: true })).toHaveCount(0)
})
