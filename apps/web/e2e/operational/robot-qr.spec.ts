import { expect, test } from '@playwright/test'
import jsQR from 'jsqr'
import { installOperational, snapshot } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

for (const width of [320, 1440]) {
  test(`QR contains the exact VIN and prints isolated labels at ${width}px`, async ({ page, context, browserName }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'mechanic' })
    await context.addInitScript(() => { window.print = () => { document.documentElement.dataset.printed = 'yes' } })
    await page.goto('/robots?park=7')
    await page.getByRole('button', { name: 'QR и печать' }).click()
    await page.getByLabel('Номер или VIN для QR').fill('1441')
    const image = page.getByRole('img', { name: 'QR: YASADR00000001441' })
    await expect(image).toBeVisible()
    const pixels = await image.evaluate(async element => {
      const img = element as HTMLImageElement
      await img.decode()
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 464
      const ctx = canvas.getContext('2d')!; ctx.drawImage(img, 0, 0, 464, 464)
      return Array.from(ctx.getImageData(0, 0, 464, 464).data)
    })
    expect(jsQR(new Uint8ClampedArray(pixels), 464, 464)?.data).toBe('YASADR00000001441')
    await assertNoSeriousA11yViolations(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath('qr-dialog.png'), fullPage: true })
    const copies = width === 320 ? 2 : 16
    await page.getByLabel('Копий', { exact: true }).fill(String(copies))
    if (width === 320) {
      await page.getByLabel('Бумага', { exact: true }).selectOption('label')
      await page.getByLabel('Ширина, мм', { exact: true }).fill('40')
      await page.getByLabel('Высота, мм', { exact: true }).fill('30')
    }
    const popupEvent = page.waitForEvent('popup')
    await page.getByRole('button', { name: 'Печатать', exact: true }).click()
    const popup = await popupEvent
    await expect(popup.locator('section')).toHaveCount(copies)
    await expect(popup.locator('p').first()).toHaveText('YASADR00000001441')
    await expect(popup.locator('nav, button, script')).toHaveCount(0)
    await expect(popup.locator('html')).toHaveAttribute('data-printed', 'yes')
    await popup.emulateMedia({ media: 'print' })
    // All engines exercise the print document; Playwright's PDF export is a
    // Chromium-only API (it is not the user's print dialog).
    if (browserName === 'chromium') {
      const pdf = await popup.pdf({ path: info.outputPath(width === 320 ? 'label-40x30.pdf' : 'labels-a4.pdf'), preferCSSPageSize: true })
      expect(pdf.toString('latin1').match(/\/Type \/Page\b/g)).toHaveLength(2)
    }
    await popup.close()
  })
}

test('robot card pre-fills its canonical VIN', async ({ page }) => {
  await installOperational(page, { role: 'mechanic' })
  await page.goto(`/robots/${snapshot.vin}?park=7`)
  await page.getByText('VIN и координаты', { exact: true }).click()
  await page.getByRole('button', { name: 'QR и печать' }).click()
  await expect(page.getByLabel('Номер или VIN для QR')).toHaveValue(snapshot.vin)
  await expect(page.getByRole('img', { name: `QR: ${snapshot.vin}` })).toBeVisible()
})
