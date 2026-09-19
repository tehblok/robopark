import { expect, test } from '@playwright/test'
import type { Report } from '../../src/api'
import { installOperational } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertResponsiveContracts } from './routeFixtures'

const report: Report = { id: 9, kind: 'mechanic_problem', status: 'open', park_id: 7, author_user_id: 100, target_role: 'operator', title: 'Повреждение колеса', body: 'Требуется проверка крепления', tracker_key: null, tracker_url: null, return_comment: null, parent_report_id: null, created_at: '2026-09-02T09:00:00Z', updated_at: '2026-09-02T09:00:00Z', resolved_at: null }

for (const role of ['admin', 'royal'] as const) test(`${role} deletes report only after confirmed successful response in A`, async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  let deletes = 0
  await installOperational(page, { role, routes: [
    { method: 'GET', path: '/api/reports/inbox', handler: () => ({ json: deletes > 1 ? [] : [report] }) },
    { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/reports/9', handler: () => ({ json: report }) },
    { method: 'DELETE', path: '/api/reports/9', handler: () => { deletes++; return deletes === 1 ? { status: 503, json: { detail: 'offline' } } : { json: { ok: true } } } },
  ] })
  await page.goto('/reports/9?park=7&pane=inbox')
  await selectInterface(page, 'Новый А')
  await expect(page.getByRole('heading', { name: report.title, exact: true })).toBeVisible()
  await assertResponsiveContracts(page, 390)
  await page.evaluate(() => { (document.activeElement as HTMLElement)?.blur(); window.scrollTo(0, 0) })
  await page.screenshot({ path: info.outputPath('report-a.png'), fullPage: true })
  await page.getByRole('button', { name: 'Удалить репорт', exact: true }).click()
  const dialog = page.getByRole('alertdialog')
  await expect(dialog.getByRole('button', { name: 'Удалить безвозвратно' })).toBeDisabled()
  await dialog.getByRole('textbox').fill('УДАЛИТЬ')
  await dialog.getByRole('button', { name: 'Удалить безвозвратно' }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(page.getByRole('heading', { name: report.title, exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Удалить безвозвратно' }).click()
  await expect(page.getByRole('heading', { name: report.title, exact: true })).toHaveCount(0)
  expect(deletes).toBe(2)
})

test('report A draft retains photo on switching while 403 hides denied detail', async ({ page }) => {
  await installOperational(page, { routes: [{ method: 'GET', path: '/api/reports/9', handler: () => ({ status: 403, json: { detail: 'forbidden' } }) }] })
  await page.goto('/reports/new?park=7')
  await selectInterface(page, 'Новый А')
  await page.getByRole('button', { name: 'Проблема', exact: true }).click()
  await page.getByRole('textbox', { name: 'Заголовок *' }).fill('Нужна помощь')
  await page.getByLabel('Файл', { exact: true }).setInputFiles({ name: 'robot.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('photo') })
  await selectInterface(page, 'Классический')
  await selectInterface(page, 'Новый А')
  await expect(page.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Нужна помощь')
  await expect(page.getByText(/Выбран файл: robot.jpg/)).toBeVisible()
  await page.goto('/reports/9?park=7')
  await expect(page.locator('.report-detail')).toHaveCount(0)
  await selectInterface(page, 'Классический')
  await expect(page.locator('.report-detail')).toHaveCount(0)
})
