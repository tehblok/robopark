import { expect, test } from '@playwright/test'
import { installOperational, userForRole } from './fixtures'
import { openRouteFixture } from './routeFixtures'

for (const width of [390, 1440]) {
  test(`Classic preserves report draft and attachment at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'mechanic' })
    await page.goto('/reports/new?park=7')
    await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
    await expect(page.getByRole('radiogroup', { name: 'Интерфейс' })).toHaveCount(0)
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

test('automatic sync status stays centered and does not present a manual sync action', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openRouteFixture(page, 'overview', userForRole('mechanic'))
  const indicator = page.getByRole('button', { name: 'Открыть центр синхронизации. Синхронизация выполняется автоматически' })
  await expect(indicator).toBeVisible()
  await expect(page.getByRole('button', { name: /(?:синхронизировать|запустить синхронизацию)/i })).toHaveCount(0)
  const centers = await page.locator('.rp-sync-center, .rp-shell__topbar').evaluateAll(([sync, topbar]) => {
    const syncBox = sync.getBoundingClientRect()
    const topbarBox = topbar.getBoundingClientRect()
    return [(syncBox.left + syncBox.right) / 2, (topbarBox.left + topbarBox.right) / 2]
  })
  expect(Math.abs(centers[0]! - centers[1]!)).toBeLessThanOrEqual(1)
})

for (const width of [390, 1440] as const) {
  test(`park selector stays compact and switches the active park at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, 'overview', userForRole('operator'))
    await page.getByRole('button', { name: 'Сменить парк', exact: true }).click()
    const selector = page.getByRole('listbox', { name: 'Сменить парк', exact: true })
    await expect(selector).toBeVisible()
    const geometry = await selector.evaluate(element => {
      const box = element.getBoundingClientRect()
      return { left: box.left, right: box.right, width: box.width }
    })
    expect(geometry.left).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(width)
    expect(geometry.width).toBeLessThanOrEqual(Math.min(width - 24, 288))
    await selector.getByRole('option', { name: 'Южный парк', exact: true }).click()
    await expect(page).toHaveURL(/park=8/)
    await expect(page.getByText('Южный парк', { exact: true }).first()).toBeVisible()
  })
}

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
