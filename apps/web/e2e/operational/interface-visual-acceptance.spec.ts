import { expect, test } from '@playwright/test'
import { openRouteFixture, assertResponsiveContracts } from './routeFixtures'
import { userForRole, settlePage } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

const screens = ['overview', 'work', 'work-issue', 'robots', 'robot-check', 'inventory', 'reports', 'reports-new', 'campaigns', 'campaign-detail', 'analytics', 'admin-users', 'admin-roles', 'admin-settings', 'admin-robot-check'] as const
test.describe.configure({ mode: 'parallel' })
for (const mode of ['Классический', 'Новый А'] as const)
  for (const theme of ['light', 'dark'] as const)
    for (const width of [320, 390, 412, 768, 1024, 1440] as const)
      for (const route of screens) {
  test(`${mode} visual ${route} ${theme} ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await openRouteFixture(page, route, userForRole('royal'))
    await selectInterface(page, mode)
    await settlePage(page)
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    const name = `${mode === 'Новый А' ? 'a' : 'classic'}-${route}-${theme}-${width}.png`
    if (width === 390 || width === 1440)
      await expect(page).toHaveScreenshot(name, { fullPage: true, animations: 'disabled', caret: 'hide' })
    else
      await page.screenshot({ path: info.outputPath(name), fullPage: true, animations: 'disabled' })
  })
}
