import { test } from '@playwright/test'
import { openRouteFixture, assertResponsiveContracts } from './routeFixtures'
import { userForRole, settlePage } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

const screens = ['overview', 'work', 'work-issue', 'robots', 'robot-check', 'inventory', 'reports', 'reports-new', 'campaigns', 'campaign-detail', 'analytics', 'admin-users', 'admin-roles', 'admin-settings', 'admin-robot-check'] as const
for (const theme of ['light', 'dark'] as const) for (const width of [390, 1440]) for (const route of screens) {
  test(`A visual ${route} ${theme} ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await openRouteFixture(page, route, userForRole('royal'))
    await selectInterface(page, 'Новый А')
    await settlePage(page)
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
    await page.screenshot({ path: info.outputPath(`${route}-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
  })
}
