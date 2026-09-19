import { expect, test } from '@playwright/test'
import { installOperational, settlePage } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

const sections = [{ id: 'status', title: 'Статус', is_enabled: true, roles: ['mechanic', 'admin'], fields: [{ id: 9, path: 'data.status', label: 'Статус', sort_order: 0 }], sort_order: 0 }]

for (const theme of ['light', 'dark']) {
  test(`legacy Tracker route opens readable shared work cards in ${theme}`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, { role: 'admin' })
    await page.goto('/admin/tracker?park=7')
    await expect(page.locator('.rp-work-entities').first()).toBeVisible()
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)
  })
}

for (const theme of ['light', 'dark']) for (const width of [390, 1440]) {
  test(`shared admin controls reuse cache and preserve drafts at ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    let reads = 0
    await installOperational(page, { role: 'admin', routes: [{ method: 'GET', path: '/api/admin/emergency/sections', handler: async () => {
      reads += 1
      return { json: sections }
    } }] })
    await page.goto('/admin/emergency/config?park=7')
    await page.getByRole('button', { name: 'Открыть раздел Статус' }).click()
    await expect(page.getByLabel('Путь поля 9')).toHaveValue('data.status')
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    await page.screenshot({ path: info.outputPath(`admin-fields-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })

    await page.getByRole('tab', { name: 'Ошибки', exact: true }).click()
    await page.getByRole('tab', { name: 'Разделы и поля', exact: true }).click()
    await page.getByRole('button', { name: 'Открыть раздел Статус' }).click()
    await expect(page.getByLabel('Путь поля 9')).toHaveValue('data.status')
    expect(reads).toBe(1)
    await page.getByRole('button', { name: 'Новый раздел', exact: true }).click()
    await page.getByLabel('ID раздела', { exact: true }).fill('draft')
    await page.clock.setFixedTime(new Date('2026-09-02T09:08:00Z'))
    await page.evaluate(() => {
      window.dispatchEvent(new Event('focus'))
      window.dispatchEvent(new Event('online'))
    })
    await expect(page.getByLabel('ID раздела', { exact: true })).toHaveValue('draft')
    expect(reads).toBe(1)
    await expect(page.getByRole('button', { name: /^Обновить данные/ })).toHaveCount(0)
    await page.screenshot({ path: info.outputPath(`cached-draft-${theme}-${width}.png`), animations: 'disabled' })
    await expect(page.getByRole('progressbar')).toBeHidden()
  })
}
