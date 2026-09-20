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

test('unknown address preserves a neutral pre-authentication presentation', async ({ page }) => {
  await installMockApi(page, { user: null })
  await page.goto('/missing-interface-route')
  await expect(page.getByRole('heading', { name: 'Вход', exact: true })).toBeVisible()
  await expect(page.locator('html')).not.toHaveAttribute('data-interface')
  await expect(page.locator('.rp-classic-shell, .rp-task-first-shell')).toHaveCount(0)
  await expect(page).toHaveURL(/\/login$/)
})

test('persisted account A mode has no first-paint Classic shell and does not duplicate route GETs', async ({ page }) => {
  const user = userForRole('mechanic')
  await page.addInitScript(({ accountId }) => {
    localStorage.setItem(`robopark:interface:v1:${accountId}`, 'task-first')
    const modes: string[] = []
    Object.defineProperty(window, '__roboparkShellModes', { value: modes })
    new MutationObserver(() => {
      if (document.querySelector('.rp-classic-shell')) modes.push('classic')
      if (document.querySelector('.rp-task-first-shell')) modes.push('task-first')
    }).observe(document, { childList: true, subtree: true })
  }, { accountId: user.id })
  let overviewGets = 0
  page.on('request', request => {
    if (request.method() === 'GET' && request.url().includes('/api/operations/overview')) overviewGets += 1
  })
  await openRouteFixture(page, 'overview', user)
  await expect(page.locator('.rp-task-first-shell')).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as { __roboparkShellModes: string[] }).__roboparkShellModes)).not.toContain('classic')
  const before = overviewGets
  await selectInterface(page, 'Классический')
  await selectInterface(page, 'Новый А')
  expect(overviewGets).toBe(before)
})

for (const mode of ['Классический', 'Новый А'] as const)
for (const width of [320, 390, 412, 899, 1440]) for (const theme of ['light', 'dark']) {
  test(`${mode} shell visual ${theme} ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await openRouteFixture(page, 'overview', userForRole('mechanic'))
    if (mode === 'Новый А') await selectInterface(page, mode)
    const shell = page.locator(mode === 'Новый А' ? '.rp-task-first-shell' : '.rp-classic-shell')
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
    }
    await page.screenshot({ path: testInfo.outputPath(`${mode === 'Новый А' ? 'a' : 'classic'}-shell.png`), fullPage: true })
  })
}

for (const mode of ['Классический', 'Новый А'] as const)
for (const width of [390, 1440]) {
  test(`${mode} More menu stays a bounded one-column portal at ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await openRouteFixture(page, 'overview', userForRole('mechanic'))
    if (mode === 'Новый А') await selectInterface(page, mode)

    await page.getByRole('button', { name: 'Ещё', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Ещё' })
    const controls = dialog.locator('.rp-shell-controls')
    await expect(dialog).toBeVisible()
    await expect(controls).toHaveCount(1)
    await expect(dialog.locator('.rp-classic-shell, .rp-task-first-shell')).toHaveCount(0)

    const geometry = await dialog.evaluate(element => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return {
        bottom: rect.bottom,
        height: rect.height,
        overflowX: style.overflowX,
        right: rect.right,
        top: rect.top,
        width: rect.width,
      }
    })
    expect(geometry.top).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(width)
    expect(geometry.bottom).toBeLessThanOrEqual(900)
    expect(geometry.width).toBeLessThanOrEqual(width)
    expect(geometry.height).toBeLessThan(900)
    expect(geometry.overflowX).not.toBe('visible')

    const actions = await controls.locator('.rp-shell__more-link').evaluateAll(elements =>
      elements.map(element => {
        const rect = element.getBoundingClientRect()
        return { bottom: rect.bottom, left: rect.left, top: rect.top, width: rect.width }
      }),
    )
    expect(actions.length).toBeGreaterThan(1)
    for (let index = 1; index < actions.length; index += 1) {
      expect(Math.abs(actions[index].left - actions[0].left)).toBeLessThanOrEqual(1)
      expect(Math.abs(actions[index].width - actions[0].width)).toBeLessThanOrEqual(1)
      expect(actions[index].top).toBeGreaterThanOrEqual(actions[index - 1].bottom)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await dialog.screenshot({ path: testInfo.outputPath(`${mode === 'Новый А' ? 'a' : 'classic'}-more-${width}.png`) })
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
