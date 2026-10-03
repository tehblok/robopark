import { expect, test } from '@playwright/test'
import { settlePage, userForRole } from './fixtures'
import { assertResponsiveContracts, openRouteFixture } from './routeFixtures'

for (const theme of ['light', 'dark'] as const) for (const route of ['reports', 'reports-new', 'report-detail'] as const) {
  test(`${route} keeps content and actions inside a 320px ${theme} phone`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 320, height: 750 })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    await openRouteFixture(page, route, userForRole('mechanic'))
    if (route === 'reports-new') {
      await page.getByRole('textbox', { name: 'Заголовок *', exact: true }).fill('Проверка робота с повторяющейся неисправностью переднего левого колеса')
    }
    await assertResponsiveContracts(page, 320)
    const content = await page.locator('main').boundingBox()
    expect(content).toBeTruthy()
    expect(content!.x).toBeGreaterThanOrEqual(0)
    expect(content!.x + content!.width).toBeLessThanOrEqual(320)
    for (const action of await page.locator('main button:visible').all()) {
      const box = await action.boundingBox()
      expect(box, await action.getAttribute('aria-label') ?? await action.textContent() ?? 'button').toBeTruthy()
      expect(box!.x).toBeGreaterThanOrEqual(-1)
      expect(box!.x + box!.width).toBeLessThanOrEqual(321)
    }
    await page.screenshot({ path: testInfo.outputPath(`${route}-320-${theme}.png`), fullPage: true, animations: 'disabled' })
  })
}

test('report attachment uses a Russian file control and keeps the real upload input on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'reports-new', userForRole('mechanic'))
  const input = page.getByLabel('Файл', { exact: true })
  await expect(input).toBeEnabled()
  const choose = page.getByText('Выбрать файл', { exact: true })
  await expect(choose).toBeVisible()
  expect((await choose.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await input.setInputFiles({ name: 'robot.log', mimeType: 'text/plain', buffer: Buffer.from('diagnostic') })
  await expect(page.getByText(/Выбран файл: robot\.log/)).toBeVisible()
  await assertResponsiveContracts(page, 390)
})

test('report detail keeps a section gap before the create action on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'report-detail', userForRole('operator'))
  await settlePage(page)
  const detail = await page.locator('.report-composition > section').boundingBox()
  const create = await page.getByRole('link', { name: 'Создать репорт' }).boundingBox()
  expect(detail).not.toBeNull()
  expect(create).not.toBeNull()
  expect(create!.y - (detail!.y + detail!.height)).toBeGreaterThanOrEqual(16)
})

test('report creation keeps a section gap before the return action on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'reports-new', userForRole('mechanic'))
  await settlePage(page)
  const form = await page.locator('.report-composition > section').boundingBox()
  const back = await page.getByRole('link', { name: 'К репортам' }).boundingBox()
  expect(form).not.toBeNull()
  expect(back).not.toBeNull()
  expect(back!.y - (form!.y + form!.height)).toBeGreaterThanOrEqual(16)
})
