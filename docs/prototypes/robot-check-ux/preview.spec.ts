import { test, expect } from '../../../apps/web/node_modules/@playwright/test/index.mjs'
import { fileURLToPath } from 'node:url'
import { installOperational, snapshot } from '../../../apps/web/e2e/operational/fixtures'

for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
  test(`${theme}-${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    // Existing visual-test sample, not real device readings or installed mapping.
    await installOperational(page, { role: 'royal', snapshot: {
      ...snapshot, battery1_connected: true, battery2_connected: true,
      readings: [
        { id: 1, section_id: 'wheels', label: 'Левый парктроник', display: '18 см', state: 'warning', view: 'top', x: .24, y: .56, label_direction: 'left' },
        { id: 2, section_id: 'wheels', label: 'Ток колеса', display: '4,2 А', state: 'normal', view: 'top', x: .72, y: .42, label_direction: 'right' },
      ], diagnostic_events: [],
    } })
    await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
    await expect(page.locator('.rp-check-diagnostic-block')).toBeVisible()
    await page.locator('.rp-check-photo-frame img').evaluate((image: HTMLImageElement) => image.decode())
    await page.locator('.rp-check-block-photo-frame img').evaluate((image: HTMLImageElement) => image.decode())
    await page.evaluate(() => { if (document.activeElement instanceof HTMLElement) document.activeElement.blur() })
    const controls = await page.locator('.rp-check-workspace button').allTextContents()
    await page.screenshot({ path: testInfo.outputPath('before.png'), fullPage: true })
    await page.addStyleTag({ path: fileURLToPath(new URL('./proposal.css', import.meta.url)) })
    await expect(page.locator('.rp-check-first-level')).toHaveCSS('padding', width < 900 ? '12px' : '16px')
    expect(await page.locator('.rp-check-workspace button').allTextContents()).toEqual(controls)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('proposal.png'), fullPage: true })
    await page.getByRole('button', { name: 'Показание: Левый парктроник, 18 см' }).click()
    await expect(page.getByRole('button', { name: 'Показание: Левый парктроник, 18 см' })).toHaveAttribute('aria-pressed', 'true')
  })
}
