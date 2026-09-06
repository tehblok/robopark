import { expect, test } from '@playwright/test'
import { installOperational, settlePage } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

const sections = [{ id: 'status', title: 'Статус', is_enabled: true, roles: ['mechanic', 'admin'], fields: [{ id: 9, path: 'data.status', label: 'Статус', sort_order: 0 }], sort_order: 0 }]

for (const theme of ['light', 'dark']) {
  test(`shared Tracker avatars retain readable contrast in ${theme}`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, { role: 'admin' })
    await page.goto('/admin/tracker?park=7')
    await expect(page.locator('.issue-avatar').first()).toBeVisible()
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)
  })
}

for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
  test(`shared admin controls and background refresh at ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    let reads = 0
    let release: (() => void) | undefined
    await installOperational(page, { role: 'admin', routes: [{ method: 'GET', path: '/api/admin/emergency/sections', handler: async () => {
      if (++reads > 1) await new Promise<void>(resolve => { release = resolve })
      return { json: sections }
    } }] })
    await page.goto('/admin/emergency/config?park=7')
    await expect(page.getByLabel('Путь поля 9')).toHaveValue('data.status')
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: info.outputPath(`admin-fields-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })

    await page.getByRole('tab', { name: 'Ошибки и индикация', exact: true }).click()
    await page.getByRole('tab', { name: 'Разделы и поля', exact: true }).click()
    await expect.poll(() => !!release).toBe(true)
    try {
      const progress = page.getByRole('progressbar', { name: 'Обновление данных' })
      await expect(progress).toBeVisible()
      await page.getByLabel('ID раздела', { exact: true }).fill('draft')
      await expect(page.getByLabel('ID раздела', { exact: true })).toHaveValue('draft')
      await page.screenshot({ path: info.outputPath(`refresh-${theme}-${width}.png`), animations: 'disabled' })
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await expect(progress).toBeVisible()
      expect(await progress.evaluate(element => getComputedStyle(element, '::after').animationName)).toBe('none')
    } finally { release?.() }
    await expect(page.getByRole('progressbar')).toBeHidden()
  })
}
