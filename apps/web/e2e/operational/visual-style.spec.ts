import { expect, test } from '@playwright/test'
import { openRouteFixture, assertResponsiveContracts } from './routeFixtures'
import { userForRole } from './fixtures'

for (const theme of ['light', 'dark'] as const) test(`management checkboxes use the action palette in ${theme}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
  await openRouteFixture(page, 'admin-roles', userForRole('royal'))
  await expect(page.getByRole('checkbox', { name: 'Склад' })).toHaveCSS('accent-color', theme === 'dark' ? 'rgb(182, 207, 130)' : 'rgb(82, 107, 58)')
})

for (const theme of ['light', 'dark'] as const) test(`campaign link follows the action palette in ${theme}`, async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
  await openRouteFixture(page, 'campaigns', userForRole('mechanic'))
  await expect(page.locator('.campaign-card h2 a')).toHaveCSS('color', theme === 'dark' ? 'rgb(182, 207, 130)' : 'rgb(82, 107, 58)')
})

test('legacy diagnostic tabs are flat and touch-sized', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
  await page.getByRole('tab', { name: 'Ошибки', exact: true }).click()
  const tab = page.getByRole('tab', { name: 'Каталог ошибок', exact: true })
  await expect(tab).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
  expect((await tab.boundingBox())!.height).toBeGreaterThanOrEqual(44)
})

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
