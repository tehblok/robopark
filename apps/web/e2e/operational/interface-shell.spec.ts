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

    const menuLabel = width < 900 ? 'Меню' : 'Ещё'
    await page.getByRole('button', { name: menuLabel, exact: true }).click()
    const dialog = page.getByRole('dialog', { name: menuLabel })
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

for (const width of [320, 390, 412]) {
  test(`A nonempty context stays below full-width content at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, 'overview', userForRole('mechanic'))
    await selectInterface(page, 'Новый А')
    const shell = page.locator('.rp-task-first-shell')
    await shell.locator('[data-shell-zone="context"]').evaluate(element => {
      element.removeAttribute('hidden')
      element.textContent = 'Контекст робота'
    })
    await shell.locator('[data-shell-zone="action"]').evaluate(element => {
      element.removeAttribute('hidden')
      element.textContent = 'Основное действие'
    })

    const layout = await shell.evaluate(element => {
      const workspace = element.querySelector<HTMLElement>('.rp-task-first-shell__workspace')!
      const content = element.querySelector<HTMLElement>('.rp-task-first-shell__content')!
      const context = element.querySelector<HTMLElement>('.rp-task-first-shell__context')!
      const action = element.querySelector<HTMLElement>('.rp-task-first-shell__action')!
      return {
        columns: getComputedStyle(workspace).gridTemplateColumns,
        content: content.getBoundingClientRect().toJSON(),
        context: context.getBoundingClientRect().toJSON(),
        action: action.getBoundingClientRect().toJSON(),
        noOverflow: document.documentElement.scrollWidth <= innerWidth,
      }
    })
    expect(layout.columns.trim().split(/\s+/)).toHaveLength(1)
    expect(layout.content.width).toBeGreaterThan(width * 0.8)
    expect(layout.context.width).toBeGreaterThan(width * 0.8)
    expect(layout.action.width).toBeGreaterThan(width * 0.8)
    expect(layout.context.top).toBeGreaterThanOrEqual(layout.content.bottom)
    expect(layout.action.top).toBeGreaterThanOrEqual(layout.context.bottom)
    expect(layout.noOverflow).toBe(true)
  })
}

for (const width of [899, 1440]) {
  test(`A nonempty context keeps its desktop rail at ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, 'overview', userForRole('mechanic'))
    await selectInterface(page, 'Новый А')
    const shell = page.locator('.rp-task-first-shell')
    await shell.locator('[data-shell-zone="context"]').evaluate(element => {
      element.removeAttribute('hidden')
      element.textContent = 'Контекст робота'
    })
    await shell.locator('[data-shell-zone="action"]').evaluate(element => {
      element.removeAttribute('hidden')
      element.textContent = 'Основное действие'
    })

    const layout = await shell.locator('.rp-task-first-shell__workspace').evaluate(element => {
      const content = element.querySelector<HTMLElement>('.rp-task-first-shell__content')!
      const context = element.querySelector<HTMLElement>('.rp-task-first-shell__context')!
      return {
        columns: getComputedStyle(element).gridTemplateColumns,
        contentWidth: content.getBoundingClientRect().width,
        contextWidth: context.getBoundingClientRect().width,
        noOverflow: document.documentElement.scrollWidth <= innerWidth,
      }
    })
    expect(layout.columns.trim().split(/\s+/)).toHaveLength(2)
    expect(layout.contentWidth).toBeGreaterThan(0)
    expect(layout.contextWidth).toBeGreaterThanOrEqual(240)
    expect(layout.noOverflow).toBe(true)
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
