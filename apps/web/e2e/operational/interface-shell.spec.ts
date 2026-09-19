import { expect, test } from '@playwright/test'
import { installOperational, roles } from './fixtures'
import { selectInterface } from '../support/interfaceMode'
import { ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'
import { openRouteFixture } from './routeFixtures'
import { userForRole } from './fixtures'
import { installMockApi } from '../support/mockApi'

for (const role of roles) {
  test(`${role} retains navigation and context in A`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await installOperational(page, { role })
    await page.goto('/overview?park=7')
    await expect(page.locator('.rp-shell__desktop-nav')).toBeVisible()
    const links = await page.locator('.rp-shell__desktop-nav a').evaluateAll(items => items.map(item => item.getAttribute('href')))
    const before = page.url()
    await selectInterface(page, 'Новый А')
    await expect(page.locator('.rp-shell__context')).toBeVisible()
    expect(await page.locator('.rp-shell__desktop-nav a').evaluateAll(items => items.map(item => item.getAttribute('href')))).toEqual(links)
    expect(page.url()).toBe(before)
    await expect(page.locator('.rp-shell__sidebar')).toHaveCSS('width', '224px')
    await selectInterface(page, 'Классический')
    await expect(page.locator('.rp-shell__context')).toBeHidden()
  })
}

test('unknown address preserves the safe login redirect and interface choice', async ({ page }) => {
  await installMockApi(page, { user: null })
  await page.goto('/missing-interface-route')
  await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible()
  await page.getByText('Вид интерфейса', { exact: true }).click()
  await page.getByRole('radio', { name: 'Новый А', exact: true }).check()
  await expect(page.locator('html')).toHaveAttribute('data-interface', 'task-first')
  await expect(page).toHaveURL(/\/login$/)
})

for (const width of [320, 390, 1440]) for (const theme of ['light', 'dark']) {
  test(`A shell visual ${theme} ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await openRouteFixture(page, 'overview', userForRole('mechanic'))
    await selectInterface(page, 'Новый А')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('shell.png'), fullPage: true })
  })
}

for (const route of ROUTE_MANIFEST.filter(item => item.surface === 'public')) {
  test(`anonymous ${route.id} has an independent interface choice`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 })
    await installMockApi(page, { user: null })
    await page.goto(route.path)
    await page.getByText('Вид интерфейса', { exact: true }).click()
    await page.getByRole('radio', { name: 'Новый А', exact: true }).check()
    await expect(page.locator('html')).toHaveAttribute('data-interface', 'task-first')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('radio', { name: 'Классический', exact: true }).check()
    await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
  })
}
