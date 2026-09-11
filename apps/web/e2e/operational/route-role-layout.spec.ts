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
