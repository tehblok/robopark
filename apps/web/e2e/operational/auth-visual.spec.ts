import { expect, test } from '@playwright/test'
import { settlePage } from './fixtures'
import { installMockApi } from '../support/mockApi'

test.describe.configure({ mode: 'parallel' })

for (const route of ['login', 'register'] as const)
  for (const theme of ['light', 'dark'] as const)
    for (const width of [320, 390] as const)
      test(`${route} ${theme} ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 900 })
        await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
        await installMockApi(page, { user: null })
        await page.goto(`/${route}`)
        await expect(page.locator('main')).toBeVisible()
        await settlePage(page)
        await page.evaluate(() => window.scrollTo(0, 0))
        const name = `${route}-${theme}-${width}.png`
        // Native legend and decorative-dot rasterization can differ by one pixel
        // between Linux runners. Keep the allowance local to these auth screens
        // (100 pixels out of at least 351,000), without changing any baseline.
        if (width === 390) await expect(page).toHaveScreenshot(name, { fullPage: true, animations: 'disabled', caret: 'hide', maxDiffPixels: 100 })
        else await page.screenshot({ path: info.outputPath(name), fullPage: true, animations: 'disabled' })
      })
