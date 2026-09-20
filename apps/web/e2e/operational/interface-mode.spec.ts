import { expect, test } from '@playwright/test'
import { installOperational, userForRole } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { openRouteFixture } from './routeFixtures'

for (const width of [390, 1440]) {
  test(`interface preserves report draft and attachment at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'mechanic' })
    await page.goto('/reports/new?park=7')
    await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
    await page.getByRole('button', { name: 'Проблема', exact: true }).click()
    const text = page.getByRole('textbox', { name: 'Заголовок *', exact: true })
    await text.fill('Проверить крепление крышки')
    const file = page.getByLabel('Файл', { exact: true })
    await file.setInputFiles({ name: 'robot.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('photo bytes') })
    const route = page.url()
    for (const mode of ['Новый А', 'Классический', 'Новый А'] as const) {
      await selectInterface(page, mode)
      await expect(text).toHaveValue('Проверить крепление крышки')
      expect(page.url()).toBe(route)
      await expect(page.getByText(/Выбран файл: robot.jpg/)).toBeVisible()
    }
    await page.reload()
    await expect(page.locator('html')).toHaveAttribute('data-interface', 'task-first')
  })
}

test('real API write defers mode change until the response and is not resent', async ({ page }) => {
  let finish!: () => void
  const gate = new Promise<void>(resolve => { finish = resolve })
  let requests = 0
  await installOperational(page, { role: 'mechanic', routes: [
    { method: 'POST', path: '/api/reports', handler: async () => {
      requests++
      await gate
      return { status: 503, json: { detail: 'offline' } }
    } },
  ] })
  await page.goto('/reports/new?park=7')
  await page.getByRole('button', { name: 'Проблема', exact: true }).click()
  await page.getByRole('textbox', { name: 'Заголовок *' }).fill('Крепление крышки')
  await page.getByRole('button', { name: 'Создать', exact: true }).click()
  await expect.poll(() => requests).toBe(1)
  await page.getByRole('button', { name: 'Ещё', exact: true }).click()
  await page.getByRole('radio', { name: 'Новый А', exact: true }).check()
  await expect(page.getByText('Переключим после завершения операции')).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
  finish()
  await expect(page.locator('html')).toHaveAttribute('data-interface', 'task-first')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('Крепление крышки')
  expect(requests).toBe(1)
})

test('admin role, user and settings drafts keep their live controls and open workspaces across modes', async ({ page }) => {
  const royal = userForRole('royal')
  await openRouteFixture(page, 'admin-roles', royal)
  await page.getByRole('button', { name: 'Открыть роль Механик', exact: true }).click()
  const roleName = page.getByLabel('Название', { exact: true })
  await roleName.fill('Механик — черновик')
  await roleName.evaluate(element => { (element as HTMLInputElement & { __modeIdentity?: string }).__modeIdentity = 'role' })
  await selectInterface(page, 'Новый А')
  await expect(roleName).toHaveValue('Механик — черновик')
  expect(await roleName.evaluate(element => (element as HTMLInputElement & { __modeIdentity?: string }).__modeIdentity)).toBe('role')
  await expect(page.getByRole('heading', { name: /Редактор: Механик/ })).toBeVisible()

  await openRouteFixture(page, 'admin-users', royal)
  await page.getByRole('button', { name: 'Открыть аккаунт route-admin', exact: true }).click()
  const password = page.getByLabel('Новый пароль', { exact: true })
  await password.fill('unsaved-password')
  await password.evaluate(element => { (element as HTMLInputElement & { __modeIdentity?: string }).__modeIdentity = 'user' })
  await selectInterface(page, 'Классический')
  await expect(password).toHaveValue('unsaved-password')
  await selectInterface(page, 'Новый А')
  await expect(password).toHaveValue('unsaved-password')
  expect(await password.evaluate(element => (element as HTMLInputElement & { __modeIdentity?: string }).__modeIdentity)).toBe('user')
  await expect(page.getByRole('heading', { name: 'route-admin', exact: true })).toBeVisible()

  await openRouteFixture(page, 'admin-settings', royal)
  const token = page.getByLabel('Tracker OAuth-токен', { exact: true })
  await token.fill('unsaved-token')
  await token.evaluate(element => { (element as HTMLInputElement & { __modeIdentity?: string }).__modeIdentity = 'settings' })
  await selectInterface(page, 'Классический')
  await expect(token).toHaveValue('unsaved-token')
  await selectInterface(page, 'Новый А')
  await expect(token).toHaveValue('unsaved-token')
  expect(await token.evaluate(element => (element as HTMLInputElement & { __modeIdentity?: string }).__modeIdentity)).toBe('settings')
})
