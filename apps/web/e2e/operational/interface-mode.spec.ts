import { expect, test } from '@playwright/test'
import { installOperational, userForRole } from './fixtures'
import { openRouteFixture } from './routeFixtures'

for (const width of [390, 1440]) {
  test(`Classic preserves report draft and attachment at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'mechanic' })
    await page.goto('/reports/new?park=7')
    await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
    await page.getByRole('button', { name: 'Проблема', exact: true }).click()
    const title = page.getByRole('textbox', { name: 'Заголовок *', exact: true })
    await title.fill('Проверить крепление крышки')
    await page.getByLabel('Файл', { exact: true }).setInputFiles({ name: 'robot.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('photo bytes') })

    await expect(title).toHaveValue('Проверить крепление крышки')
    await expect(page.getByText(/Выбран файл: robot.jpg/)).toBeVisible()
    await expect(page.locator('.rp-classic-shell')).toBeVisible()
  })
}

test('real API write is not resent and keeps its draft after a failed response', async ({ page }) => {
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
  const title = page.getByRole('textbox', { name: 'Заголовок *' })
  await title.fill('Крепление крышки')
  await page.getByRole('button', { name: 'Создать', exact: true }).click()
  await expect.poll(() => requests).toBe(1)
  finish()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(title).toHaveValue('Крепление крышки')
  expect(requests).toBe(1)
})

test('admin role, user and settings drafts keep their open workspaces', async ({ page }) => {
  const royal = userForRole('royal')
  await openRouteFixture(page, 'admin-roles', royal)
  await page.getByRole('button', { name: 'Открыть роль Механик', exact: true }).click()
  const roleName = page.getByLabel('Название', { exact: true })
  await roleName.fill('Механик — черновик')
  await expect(roleName).toHaveValue('Механик — черновик')
  await expect(page.getByRole('heading', { name: /Редактор: Механик/ })).toBeVisible()

  await openRouteFixture(page, 'admin-users', royal)
  await page.getByRole('button', { name: 'Открыть аккаунт route-admin', exact: true }).click()
  const password = page.getByLabel('Новый пароль', { exact: true })
  await password.fill('unsaved-password')
  await expect(password).toHaveValue('unsaved-password')
  await expect(page.getByRole('heading', { name: 'route-admin', exact: true })).toBeVisible()

  await openRouteFixture(page, 'admin-settings', royal)
  const token = page.getByLabel('Tracker OAuth-токен', { exact: true })
  await token.fill('unsaved-token')
  await expect(token).toHaveValue('unsaved-token')
})
