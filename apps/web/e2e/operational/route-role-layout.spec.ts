import { expect, test } from '@playwright/test'
import { canAccessRoute } from '../../src/app/routing/accessPolicy'
import { ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { roles, userForRole } from './fixtures'
import { assertResponsiveContracts, openRouteFixture } from './routeFixtures'

const widths = [320, 390, 768, 1024, 1440] as const

for (const role of roles) for (const route of ROUTE_MANIFEST.filter(item => item.surface === 'shell')) for (const width of widths) {
  test(`${role}: ${route.id} at ${width}px`, async ({ page }) => {
    const user = userForRole(role)
    test.skip(!canAccessRoute(user, route.id), 'route denied by access policy')
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, route.id, user)
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
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
  await openRouteFixture(page, 'operator-parks', userForRole('operator'))
  await expect(page.getByText('Северный парк', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Запросить парк', exact: true }).first().click()
  await expect(page.getByRole('dialog', { name: 'Запросить парк' }).getByLabel('Парк', { exact: true })).toHaveValue('8')
  await expect(page.getByText('operator-e2e', { exact: true })).toBeVisible()

  await openRouteFixture(page, 'campaigns', userForRole('mechanic'))
  await expect(page.getByRole('heading', { name: 'Осенняя сервисная кампания', exact: true })).toBeVisible()
  await openRouteFixture(page, 'campaign-detail', userForRole('mechanic'))
  await expect(page.getByText('ROBOPARK-42', { exact: true })).toBeVisible()

  await openRouteFixture(page, 'report-detail', userForRole('mechanic'))
  await expect(page.getByText('Робот требует осмотра.', { exact: true })).toBeVisible()

  for (const routeId of ['admin-settings', 'admin-users', 'admin-roles', 'admin-robot-check'] as const) {
    await openRouteFixture(page, routeId, userForRole('royal'))
    const marker = routeId === 'admin-settings'
      ? page.getByText('Tracker OAuth', { exact: true })
      : routeId === 'admin-users'
        ? page.getByRole('button', { name: 'Открыть аккаунт route-admin', exact: true })
        : routeId === 'admin-roles'
          ? page.getByText('Механик', { exact: true })
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
    await expect(page.getByText('route-inventory-part', { exact: true })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveCount(0)
  }
  expect(canAccessRoute(userForRole('driver'), 'inventory')).toBe(false)
})
