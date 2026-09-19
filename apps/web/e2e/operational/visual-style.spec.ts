import { expect, test } from '@playwright/test'
import { openRouteFixture, assertResponsiveContracts } from './routeFixtures'
import { userForRole } from './fixtures'

for (const theme of ['light', 'dark'] as const) test(`legacy operator parks use flat panels in ${theme}`, async ({ page }) => {
  await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
  await openRouteFixture(page, 'operator-parks', userForRole('operator'))
  await expect(page.locator('.panel').first()).toHaveCSS('box-shadow', 'none')
})

for (const theme of ['light', 'dark'] as const) for (const width of [390, 1440]) {
  for (const route of ['work-issue', 'robot-check', 'inventory'] as const) {
    test(`${route}: calm surfaces in ${theme} at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 1000 })
      await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
      await openRouteFixture(page, route, userForRole('royal'))
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await assertResponsiveContracts(page, width)
      if (route === 'robot-check') {
        await expect(page.locator('.rp-check-summary')).toHaveCSS('box-shadow', 'none')
        await expect(page.locator('.rp-check-summary-values > div').first()).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
        await expect(page.locator('.rp-check-telemetry > div').first()).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
      } else if (route === 'inventory') {
        await expect(page.locator('.inventory-kpis .rp-metric-card').first()).toHaveCSS('box-shadow', 'none')
      } else {
        await expect(page.locator('.rp-work-detail-pane > .rp-panel')).toHaveCSS('box-shadow', 'none')
        await expect(page.locator('.rp-work-detail-pane .issue-detail')).toHaveCSS('border-top-width', '0px')
      }
      await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur() })
      await page.screenshot({ path: testInfo.outputPath(`${route}-${theme}-${width}.png`), fullPage: true })
    })
  }
}
