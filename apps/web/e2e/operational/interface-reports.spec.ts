import { expect, test, type Page } from '@playwright/test'
import type { Report } from '../../src/api'
import { installOperational } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { assertResponsiveContracts } from './routeFixtures'

const report: Report = { id: 9, kind: 'mechanic_problem', status: 'open', park_id: 7, author_user_id: 100, target_role: 'operator', title: 'Повреждение колеса', body: 'Требуется проверка крепления', tracker_key: null, tracker_url: null, return_comment: null, parent_report_id: null, created_at: '2026-09-02T09:00:00Z', updated_at: '2026-09-02T09:00:00Z', resolved_at: null }

const managerDeleteCases = [
  { role: 'admin' as const, mode: 'Классический' as const, width: 390 },
  { role: 'admin' as const, mode: 'Новый А' as const, width: 1440 },
  { role: 'royal' as const, mode: 'Классический' as const, width: 1440 },
  { role: 'royal' as const, mode: 'Новый А' as const, width: 390 },
]

async function openDeleteDialog(page: Page) {
  await expect(page.getByRole('button', { name: 'Удалить репорт', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Удалить репорт', exact: true }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Удалить репорт?' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText(`Репорт «${report.title}» и его вложения будут удалены без возможности восстановления.`)
  await expect(dialog.getByRole('textbox', { name: 'Введите УДАЛИТЬ для подтверждения', exact: true })).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Удалить безвозвратно' })).toBeDisabled()
  await dialog.getByRole('textbox').fill('УДАЛИТЬ')
  return dialog
}

for (const item of managerDeleteCases) {
  test(`${item.role} hard-deletes a fixture report once after exact confirmation in ${item.mode} at ${item.width}`, async ({ page }) => {
    await page.setViewportSize({ width: item.width, height: 900 })
    const requests: Array<{ method: string; path: string }> = []
    let deleted = false
    await installOperational(page, { role: item.role, routes: [
      { method: 'GET', path: '/api/reports/inbox', handler: () => ({ json: deleted ? [] : [report] }) },
      { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [] }) },
      { method: 'GET', path: '/api/reports/9', handler: () => ({ json: report }) },
      { method: 'DELETE', path: '/api/reports/9', handler: request => { requests.push({ method: request.method, path: new URL(request.url).pathname }); deleted = true; return { status: 204 } } },
    ] })
    await page.goto('/reports/9?park=7&pane=inbox')
    await selectInterface(page, item.mode)
    await expect(page.getByRole('heading', { name: report.title, exact: true })).toBeVisible()
    await assertResponsiveContracts(page, item.width)

    const dialog = await openDeleteDialog(page)
    await dialog.getByRole('button', { name: 'Удалить безвозвратно' }).click()

    await expect(page).toHaveURL(/\/reports\?park=7&pane=inbox$/)
    await expect(page.getByRole('heading', { name: report.title, exact: true })).toHaveCount(0)
    expect(requests).toEqual([{ method: 'DELETE', path: '/api/reports/9' }])
  })

  for (const failure of [403, 503]) test(`${item.role} keeps the fixture and alerts on hard-delete ${failure} in ${item.mode} at ${item.width}`, async ({ page }) => {
    await page.setViewportSize({ width: item.width, height: 900 })
    let deletes = 0
    await installOperational(page, { role: item.role, routes: [
      { method: 'GET', path: '/api/reports/inbox', handler: () => ({ json: [report] }) },
      { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [] }) },
      { method: 'GET', path: '/api/reports/9', handler: () => ({ json: report }) },
      { method: 'DELETE', path: '/api/reports/9', handler: () => { deletes++; return { status: failure, json: { detail: failure === 403 ? 'forbidden' : 'offline' } } } },
    ] })
    await page.goto('/reports/9?park=7&pane=inbox')
    await selectInterface(page, item.mode)

    const dialog = await openDeleteDialog(page)
    await dialog.getByRole('button', { name: 'Удалить безвозвратно' }).click()

    await expect(dialog.getByRole('alert')).toBeVisible()
    await expect(page.getByRole('heading', { name: report.title, exact: true })).toBeVisible()
    await expect(page).toHaveURL(/\/reports\/9\?park=7&pane=inbox$/)
    expect(deletes).toBe(1)
  })
}

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
