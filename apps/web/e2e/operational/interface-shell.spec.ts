import { expect, test } from '@playwright/test'
import { installOperational, roles, userForRole } from './fixtures'
import { ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'
import { openRouteFixture } from './routeFixtures'
import { installMockApi } from '../support/mockApi'

for (const role of roles) {
  test(`${role} retains Classic navigation and route context`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await installOperational(page, { role })
    await page.goto('/overview?park=7')
    const shell = page.locator('.rp-classic-shell')
    await expect(shell).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
    await expect(shell.locator('[data-shell-zone="navigation"]')).toHaveCount(1)
    await expect(shell.locator('[data-shell-zone="content"]')).toHaveCount(1)
    await expect(page).toHaveURL(/\/overview\?park=7/)
  })
}

test('unknown address preserves a neutral pre-authentication presentation', async ({ page }) => {
  await installMockApi(page, { user: null })
  await page.goto('/missing-interface-route')
  await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible()
  await expect(page.locator('html')).not.toHaveAttribute('data-interface')
  await expect(page.locator('.rp-classic-shell')).toHaveCount(0)
  await expect(page).toHaveURL(/\/login$/)
})

test('stored legacy preference migrates synchronously to Classic without duplicate route GETs', async ({ page }) => {
  const user = userForRole('mechanic')
  await page.addInitScript(({ accountId, legacyValue }) => {
    localStorage.setItem(`robopark:interface:v1:${accountId}`, legacyValue)
    const presentations: string[] = []
    Object.defineProperty(window, '__roboparkPresentations', { value: presentations })
    new MutationObserver(() => {
      const value = document.documentElement.getAttribute('data-interface')
      if (value) presentations.push(value)
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-interface'] })
  }, { accountId: user.id, legacyValue: ['task', 'first'].join('-') })
  let overviewGets = 0
  page.on('request', request => {
    if (request.method() === 'GET' && request.url().includes('/api/operations/overview')) overviewGets += 1
  })
  await openRouteFixture(page, 'overview', user)
  await expect(page.locator('.rp-classic-shell')).toBeVisible()
  await expect(page.locator('html')).toHaveAttribute('data-interface', 'classic')
  expect(await page.evaluate(() => (window as unknown as { __roboparkPresentations: string[] }).__roboparkPresentations.every(value => value === 'classic'))).toBe(true)
  expect(await page.evaluate(accountId => localStorage.getItem(`robopark:interface:v1:${accountId}`), user.id)).toBeNull()
  expect(overviewGets).toBe(1)
})

for (const width of [320, 390, 412, 899, 1440]) for (const theme of ['light', 'dark']) {
  test(`Classic shell visual ${theme} ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await openRouteFixture(page, 'overview', userForRole('mechanic'))
    const shell = page.locator('.rp-classic-shell')
    await expect(shell).toBeVisible()
    await expect(shell.locator('[data-shell-zone="navigation"]')).toHaveCount(1)
    await expect(shell.locator('[data-shell-zone="header"]')).toHaveCount(1)
    await expect(shell.locator('[data-shell-zone="content"]')).toHaveCount(1)
    await expect(shell.locator('[data-shell-zone="context"]')).toHaveCount(1)
    await expect(shell.locator('[data-shell-zone="action"]')).toHaveCount(1)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    if (width < 900) {
      for (const label of await page.locator('.rp-shell__bottom-nav .rp-shell__nav-label:visible').all()) {
        expect(await label.evaluate(element => element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight)).toBe(true)
      }
      const navigation = page.locator('.rp-shell__bottom-nav')
      const navBox = await navigation.boundingBox()
      const itemBoxes = await navigation.locator(':scope > a, :scope > button').evaluateAll(elements => elements.map(element => {
        const rect = element.getBoundingClientRect()
        return { left: rect.left, right: rect.right, width: rect.width }
      }))
      expect(navBox).toBeTruthy()
      expect(itemBoxes.length).toBeGreaterThanOrEqual(4)
      for (const item of itemBoxes.slice(1)) expect(Math.abs(item.width - itemBoxes[0].width)).toBeLessThanOrEqual(1)
      expect(itemBoxes[0].left - navBox!.x).toBeLessThanOrEqual(8)
      expect(navBox!.x + navBox!.width - itemBoxes.at(-1)!.right).toBeLessThanOrEqual(8)
      const contentPaddingBottom = await shell.locator('[data-shell-zone="content"]').evaluate(element => Number.parseFloat(getComputedStyle(element).paddingBottom))
      expect(contentPaddingBottom).toBeGreaterThanOrEqual(88)
    }
    await page.screenshot({ path: testInfo.outputPath('classic-shell.png'), fullPage: true })
  })
}

for (const width of [390, 1440]) {
  test(`Classic More menu stays a bounded one-column portal at ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await openRouteFixture(page, 'overview', userForRole('mechanic'))

    const menuLabel = width < 900 ? 'Меню' : 'Ещё'
    await page.getByRole('button', { name: menuLabel, exact: true }).click()
    const dialog = page.getByRole('dialog', { name: menuLabel })
    const controls = dialog.locator('.rp-shell-controls')
    await expect(dialog).toBeVisible()
    await expect(controls).toHaveCount(1)
    await expect(dialog.locator('.rp-classic-shell')).toHaveCount(0)

    const geometry = await dialog.evaluate(element => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return { bottom: rect.bottom, height: rect.height, overflowX: style.overflowX, right: rect.right, top: rect.top, width: rect.width }
    })
    expect(geometry.top).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(width)
    expect(geometry.bottom).toBeLessThanOrEqual(900)
    expect(geometry.width).toBeLessThanOrEqual(width)
    expect(geometry.height).toBeLessThan(900)
    expect(geometry.overflowX).not.toBe('visible')

    const actions = await controls.locator('.rp-shell__more-link').evaluateAll(elements => elements.map(element => {
      const rect = element.getBoundingClientRect()
      return { bottom: rect.bottom, left: rect.left, top: rect.top, width: rect.width }
    }))
    expect(actions.length).toBeGreaterThan(1)
    for (let index = 1; index < actions.length; index += 1) {
      expect(Math.abs(actions[index].left - actions[0].left)).toBeLessThanOrEqual(1)
      expect(Math.abs(actions[index].width - actions[0].width)).toBeLessThanOrEqual(1)
      expect(actions[index].top).toBeGreaterThanOrEqual(actions[index - 1].bottom)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await dialog.screenshot({ path: testInfo.outputPath(`classic-more-${width}.png`) })
  })
}

for (const route of ROUTE_MANIFEST.filter(item => item.surface === 'public')) {
  test(`anonymous ${route.id} stays presentation-neutral`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 800 })
    await installMockApi(page, { user: null })
    await page.goto(route.path)
    await expect(page.locator('html')).not.toHaveAttribute('data-interface')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
