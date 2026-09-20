import { expect, test } from '@playwright/test'
import { FIXED_TIME, installOperational, issue, snapshot } from './fixtures'
import { selectInterface } from '../support/interfaceMode'

const repair = { ...issue, claim: { park_id: 7 }, workflow: {
  owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
  display_status: 'in_progress' as const, sync_state: 'synced' as const, has_current_cycle_comment: true,
} }
const photo = { name: 'repair.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jA/0AAAAASUVORK5CYII=', 'base64') }

for (const width of [390, 1440]) {
  test(`repair chat check preserve draft and photo across interfaces ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    let snapshotRequests = 0
    page.on('request', request => { if (new URL(request.url()).pathname.includes('/snapshot')) snapshotRequests++ })
    await installOperational(page, { issue: repair, routes: [
      { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/timeline', handler: () => ({ json: [{ id: 'm1', kind: 'tracker', author: 'Оператор', text: 'Проверить колесо перед выдачей', created_at: FIXED_TIME, sync_state: 'synced', attachments: [] }] }) },
    ] })
    await page.goto('/work/ROBOPARK-42?park=7')
    await selectInterface(page, 'Новый А')
    await expect(page.getByRole('tab', { name: 'Ремонт', exact: true })).toBeVisible()
    await expect.poll(() => snapshotRequests).toBe(1)
    const taskLoaded = snapshotRequests
    await selectInterface(page, 'Классический')
    await expect(page.locator('.a-task-sequence')).toHaveCount(0)
    await selectInterface(page, 'Новый А')
    expect(snapshotRequests).toBe(taskLoaded)
    const comment = page.getByRole('textbox', { name: 'Комментарии', exact: true })
    await comment.fill('Колесо заменено, крепление проверено')
    await page.getByLabel('Выбрать фото', { exact: true }).setInputFiles(photo)
    await page.getByRole('tab', { name: 'Чат', exact: true }).click()
    await expect(page.getByText('Проверить колесо перед выдачей', { exact: true })).toBeVisible()
    await expect(comment).toHaveValue('Колесо заменено, крепление проверено')
    await selectInterface(page, 'Классический')
    await expect(comment).toHaveValue('Колесо заменено, крепление проверено')
    expect(await page.getByLabel('Выбрать фото', { exact: true }).evaluate((input: HTMLInputElement) => input.files?.[0]?.name)).toBe('repair.png')
    await selectInterface(page, 'Новый А')
    await page.getByRole('tab', { name: 'Проверка', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Состояние робота' })).toBeVisible()
    const loaded = snapshotRequests
    expect(loaded).toBeGreaterThan(0)
    await selectInterface(page, 'Классический')
    await selectInterface(page, 'Новый А')
    expect(snapshotRequests).toBe(loaded)
    await page.getByRole('tab', { name: 'Ремонт', exact: true }).click()
    await expect(comment).toHaveValue('Колесо заменено, крепление проверено')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('repair-a.png'), fullPage: true })
  })
}

test('robot check uses one snapshot owner in both interfaces', async ({ page }) => {
  let snapshots = 0
  page.on('request', request => { if (new URL(request.url()).pathname.includes('/snapshot')) snapshots++ })
  await installOperational(page, { role: 'royal' })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await expect(page.locator('.rp-check-summary')).toBeVisible()
  const loaded = snapshots
  await selectInterface(page, 'Новый А')
  await expect(page.locator('.a-robot-layout')).toBeVisible()
  await selectInterface(page, 'Классический')
  expect(snapshots).toBe(loaded)
})

test('A footer approval locks synchronously, reports 503 and recovers without an unhandled rejection', async ({ page }) => {
  const workflow = { owner: { display: 'Механик смены', login: 'mechanic-e2e' }, review_state: 'pending' as const,
    display_status: 'review' as const, sync_state: 'saved' as const, has_current_cycle_comment: true }
  let approvals = 0
  const pageErrors: Error[] = []
  page.on('pageerror', error => pageErrors.push(error))
  await installOperational(page, { role: 'operator', issue: { ...issue, workflow }, routes: [
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/review/approve', handler: () => {
      approvals++
      if (approvals === 1) return { status: 503, json: { detail: 'unavailable' } }
      return { json: { key: issue.key, action: 'approve-review', status: 'Закрыт', actor: 'operator.test', performed_at: FIXED_TIME, sync_state: 'saved', workflow: { ...workflow, review_state: 'closed', display_status: 'closed' } } }
    } },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')
  await selectInterface(page, 'Новый А')
  const action = page.getByTestId('task-action-zone').getByRole('button', { name: 'Принять и закрыть' })
  await action.evaluate(button => { button.click(); button.click() })
  await expect.poll(() => approvals).toBe(1)
  await expect(page.getByRole('alert')).toBeVisible()
  expect(pageErrors).toEqual([])
  await action.click()
  await expect.poll(() => approvals).toBe(2)
  await expect(page).toHaveURL(/\/work\?park=7/)
})

test('A parts step opens the existing disclosure and focuses its mounted content', async ({ page }) => {
  await installOperational(page, { issue: repair })
  await page.goto('/work/ROBOPARK-42?park=7')
  await selectInterface(page, 'Новый А')
  const disclosure = page.getByRole('button', { name: 'Списать запчасть' })
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  await page.getByRole('button', { name: 'Списать или заказать' }).click()
  await expect(disclosure).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('#parts')).toBeVisible()
  await expect(page.locator('#parts')).toBeFocused()
})

test('denied robot check does not reveal readings after changing interface', async ({ page }) => {
  await installOperational(page, { role: 'royal', routes: [
    { method: 'GET', path: /^\/api\/emergency\/[^/]+\/snapshot$/, handler: () => ({ status: 403, json: { detail: 'forbidden' } }) },
  ] })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await expect(page.locator('.rp-check-summary')).toHaveCount(0)
  await selectInterface(page, 'Новый А')
  await expect(page.locator('.rp-check-summary')).toHaveCount(0)
  await selectInterface(page, 'Классический')
  await expect(page.locator('.rp-check-summary')).toHaveCount(0)
})

test('review keeps defect code and one photo after failure and defers interface change', async ({ page }) => {
  let release!: () => void
  let requests = 0
  let submitted = ''
  const gate = new Promise<void>(resolve => { release = resolve })
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { issue: repair, routes: [
    { method: 'GET', path: '/api/tracker/defect-codes', handler: () => ({ json: [{ code: 'BD-01', label: 'Вмятина', description: null }] }) },
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/submit-review', handler: async request => {
      requests++
      submitted = await request.text()
      await gate
      return { status: 503, json: { detail: 'offline' } }
    } },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')
  await selectInterface(page, 'Новый А')
  await page.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
  const form = page.locator('form').filter({ has: page.getByLabel('Код дефекта') })
  await form.getByLabel('Код дефекта').fill('BD-01')
  await form.getByLabel('Выбрать файл').setInputFiles(photo)
  await selectInterface(page, 'Классический')
  await expect(form.getByLabel('Код дефекта')).toHaveValue('BD-01')
  await expect(form.getByRole('img', { name: 'Предпросмотр repair.png' })).toBeVisible()
  await form.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
  await expect.poll(() => requests).toBe(1)
  await page.locator('.rp-shell__bottom-nav').getByRole('button', { name: 'Ещё', exact: true }).click()
  await page.getByRole('radio', { name: 'Новый А', exact: true }).check()
  await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
  await expect(page.getByText('Переключим после завершения операции')).toBeVisible()
  release()
  await expect(page.locator('html')).toHaveAttribute('data-interface', 'task-first')
  await page.keyboard.press('Escape')
  await expect(form.getByLabel('Код дефекта')).toHaveValue('BD-01')
  await expect(form.getByRole('img', { name: 'Предпросмотр repair.png' })).toBeVisible()
  expect(requests).toBe(1)
  expect(submitted).toContain('BD-01')
  expect(submitted.match(/filename="repair.png"/g)).toHaveLength(1)
})
