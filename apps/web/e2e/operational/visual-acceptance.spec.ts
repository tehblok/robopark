import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { settlePage, userForRole } from './fixtures'
import { assertResponsiveContracts, openRouteFixture } from './routeFixtures'

const viewports = [360, 390, 412, 768, 1024, 1440] as const
const themes = ['light', 'dark', 'system'] as const
const densities = ['compact', 'comfortable'] as const

for (const theme of themes) for (const density of densities) for (const width of viewports) {
  test(`error catalog ${theme} ${density} ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: width < 900 ? 844 : 1000 })
    await page.emulateMedia({ colorScheme: theme === 'system' ? 'dark' : theme })
    await page.addInitScript(({ theme, density }) => {
      localStorage.setItem('robopark-theme', theme)
      localStorage.setItem('robopark-density', density)
    }, { theme, density })

    await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
    await settlePage(page)

    await expect(page).toHaveURL(/\/admin\/emergency\/config\?park=7&tab=errors/)
    await expect(page.getByRole('tab', { name: 'Ошибки', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tabpanel', { name: 'Каталог ошибок', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Открыть правило Неисправность переднего лидара', exact: true })).toBeVisible()
    await assertResponsiveContracts(page, width)
    await assertNoSeriousA11yViolations(page)
    await page.screenshot({ path: testInfo.outputPath(`error-catalog-${theme}-${density}-${width}.png`), fullPage: true })
  })
}
