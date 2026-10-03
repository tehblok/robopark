import { expect, test } from '@playwright/test'
import { openRouteFixture, assertResponsiveContracts } from './routeFixtures'
import { userForRole } from './fixtures'

for (const width of [412, 1440, 1920]) for (const route of ['inventory', 'reports', 'admin-users'] as const) {
  test(`consistent page gutter ${route} at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 915 })
    await openRouteFixture(page, route, userForRole('royal'))
    const main = await page.locator('main').boundingBox()
    const title = await page.getByRole('heading', { level: 1 }).boundingBox()
    // At 1920: 272px sidebar, then a centered 1440px content column.
    expect(Math.round(title!.x - main!.x)).toBe(width < 900 ? 16 : width === 1920 ? 104 : 24)
  })
}

test('page entry motion is short and respects reduced motion', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await openRouteFixture(page, 'inventory', userForRole('royal'))
  const content = page.locator('.rp-page-layout__content')
  expect(await content.evaluate(node => getComputedStyle(node).animationName)).not.toBe('none')
  expect(await content.evaluate(node => parseFloat(getComputedStyle(node).animationDuration))).toBeLessThanOrEqual(.18)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  expect(await content.evaluate(node => getComputedStyle(node).animationName)).toBe('none')
})

for (const width of [320, 412]) {
  test(`robot summary keeps both batteries and SIMs readable at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 915 })
    await openRouteFixture(page, 'robot-check', userForRole('royal'))
    await expect(page.getByText('АКБ 1', { exact: true })).toBeVisible()
    await expect(page.getByText('АКБ 2', { exact: true })).toBeVisible()
    await expect(page.getByRole('list', { name: 'Параметры связи' })).toContainText('SIM 1:')
    await expect(page.getByRole('list', { name: 'Параметры связи' })).toContainText('SIM 2:')
    await assertResponsiveContracts(page, width)
    await page.screenshot({ path: testInfo.outputPath('robot.png') })
  })

  test(`inventory search is reachable in first phone viewport at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 915 })
    await openRouteFixture(page, 'inventory', userForRole('royal'))
    const metrics = await page.locator('.inventory-kpis').boundingBox()
    expect(metrics!.height).toBeLessThan(220)
    const search = await page.getByRole('searchbox', { name: 'Найти запчасть' }).boundingBox()
    expect(search!.y + search!.height).toBeLessThan(650)
    await assertResponsiveContracts(page, width)
    await page.screenshot({ path: testInfo.outputPath('inventory.png') })
  })

  test(`task content replaces queue chrome on phone at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 915 })
    await openRouteFixture(page, 'work-issue', userForRole('royal'))
    await expect(page.getByRole('combobox', { name: 'Статус задач' })).toBeHidden()
    const title = await page.locator('.issue-detail-summary').boundingBox()
    expect(title!.y + title!.height).toBeLessThan(650)
    await assertResponsiveContracts(page, width)
    await page.screenshot({ path: testInfo.outputPath('task.png') })
    await page.getByRole('button', { name: 'Назад к списку' }).click()
    await expect(page.getByRole('combobox', { name: 'Статус задач' })).toBeVisible()
  })
}
