import { expect, test } from '@playwright/test'
import { openRouteFixture, assertResponsiveContracts, assertRouteSemanticContracts } from './routeFixtures'
import { userForRole, settlePage } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

const screens = ['overview', 'operator-parks', 'work', 'work-issue', 'robots', 'robot-detail', 'robot-check', 'inventory', 'reports', 'reports-new', 'report-detail', 'campaigns', 'campaign-detail', 'analytics', 'admin', 'admin-users', 'admin-roles', 'admin-settings', 'admin-robot-check'] as const
const roleFor = (route: typeof screens[number]) => route === 'operator-parks' || route === 'reports' || route === 'report-detail'
  ? 'operator' as const
  : route === 'work' || route === 'work-issue' || route === 'robots' || route === 'robot-detail' || route === 'robot-check' || route === 'inventory'
    ? 'mechanic' as const
    : 'royal' as const
test.describe.configure({ mode: 'parallel' })
for (const mode of ['Классический', 'Новый А'] as const)
  for (const theme of ['light', 'dark'] as const)
    for (const width of [320, 390, 412, 768, 1024, 1440] as const)
      for (const route of screens) {
  test(`${mode} visual ${route} ${theme} ${width}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await openRouteFixture(page, route, userForRole(roleFor(route)))
    await selectInterface(page, mode)
    await settlePage(page)
    await assertResponsiveContracts(page, width)
    await assertRouteSemanticContracts(page, route)
    await assertNoSeriousA11yViolations(page)
    await page.evaluate(async () => {
      (document.activeElement as HTMLElement | null)?.blur()
      window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
      await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    })
    const name = `${mode === 'Новый А' ? 'a' : 'classic'}-${route}-${theme}-${width}.png`
    if (width === 390 || width === 1440)
      await expect(page).toHaveScreenshot(name, { fullPage: true, animations: 'disabled', caret: 'hide' })
    else
      await page.screenshot({ path: info.outputPath(name), fullPage: true, animations: 'disabled' })
  })
}
