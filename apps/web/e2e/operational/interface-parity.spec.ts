import { expect, test } from '@playwright/test'
import { installOperational, issue } from './fixtures'
import { selectInterface } from '../support/interfaceMode'

for (const mode of ['Классический', 'Новый А'] as const) for (const role of ['mechanic', 'operator', 'driver'] as const) {
  test(`${role} cannot open protected administration in ${mode}`, async ({ page }) => {
    await installOperational(page, { role })
    await page.goto('/overview?park=7')
    await selectInterface(page, mode)
    await page.goto('/admin/users?park=7')
    await expect(page).not.toHaveURL(/\/admin\/users/)
    await expect(page.getByRole('heading', { name: 'Пользователи', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Открыть аккаунт/ })).toHaveCount(0)
  })
}

test('50 mode and work-tab cycles retain one File, draft and bounded intervals without writes', async ({ page }) => {
  test.setTimeout(120_000)
  await page.addInitScript(() => {
    const ids = new Set<number>()
    const start = window.setInterval.bind(window), stop = window.clearInterval.bind(window)
    window.setInterval = ((...args: Parameters<typeof start>) => { const id = start(...args); ids.add(id); return id }) as typeof window.setInterval
    window.clearInterval = (id) => { ids.delete(id!); stop(id) }
    Object.defineProperty(window, '__intervalCount', { get: () => ids.size })
  })
  let writes = 0
  page.on('request', request => { if (request.url().includes('/api/') && request.method() !== 'GET') writes++ })
  await installOperational(page, { issue: { ...issue, claim: { park_id: 7 }, workflow: {
    owner: { login: 'mechanic-e2e', display: 'Механик' }, review_state: null,
    display_status: 'in_progress', sync_state: 'synced', has_current_cycle_comment: true,
  } } })
  await page.goto('/work/ROBOPARK-42?park=7')
  const comment = page.getByRole('textbox', { name: 'Комментарии', exact: true })
  const file = page.getByLabel('Выбрать фото', { exact: true })
  await comment.fill('Черновик после ремонта')
  await file.setInputFiles({ name: 'repair.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jA/0AAAAASUVORK5CYII=', 'base64') })
  const count = () => page.evaluate(() => (window as unknown as { __intervalCount: number }).__intervalCount)
  const initial = await count()
  for (let cycle = 0; cycle < 50; cycle++) {
    await selectInterface(page, 'Новый А')
    await page.getByRole('tab', { name: 'Чат', exact: true }).click()
    await page.getByRole('tab', { name: 'Ремонт', exact: true }).click()
    await selectInterface(page, 'Классический')
    await expect(comment).toHaveValue('Черновик после ремонта')
    expect(await file.evaluate((input: HTMLInputElement) => input.files?.[0]?.name)).toBe('repair.png')
    expect(await count()).toBeLessThanOrEqual(initial)
  }
  expect(writes).toBe(0)
})
