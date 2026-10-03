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

for (const { role, landing } of [
  { role: 'driver', landing: '/overview' },
  { role: 'mechanic', landing: '/work' },
  { role: 'operator', landing: '/overview' },
] as const) {
  test(`${role} cannot open the System console by direct URL`, async ({ page }) => {
    const systemRequests: string[] = []
    page.on('request', request => {
      if (new URL(request.url()).pathname.startsWith('/api/admin/system/')) systemRequests.push(request.url())
    })
    await installOperational(page, { role })
    await page.goto('/system?park=7')

    await expect(page.locator('.rp-classic-shell')).toBeVisible()
    await expect.poll(() => new URL(page.url()).pathname).toBe(landing)
    await expect(page.getByRole('heading', { name: 'Система', level: 1 })).toHaveCount(0)
    expect(systemRequests).toEqual([])
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

for (const width of [320, 390]) {
  test(`mobile header keeps the selected park readable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await openRouteFixture(page, 'inventory', userForRole('mechanic'))
    const label = page.locator('.rp-shell__topbar .rp-shell__park-brand-name')
    await expect(label).toHaveText('Северный парк')
    const labelWidth = await label.evaluate(element => ({ content: element.scrollWidth, visible: element.clientWidth }))
    expect(labelWidth.content <= labelWidth.visible + 1, `park label at ${width}px: ${JSON.stringify(labelWidth)}`).toBe(true)
    const park = await page.locator('.rp-shell__topbar .rp-shell__park-brand').boundingBox()
    const sync = await page.locator('.rp-shell__topbar .rp-sync-center').boundingBox()
    expect(park && sync).toBeTruthy()
    const textInset = await label.evaluate(element => {
      const brand = element.closest('.rp-shell__park-brand')!.getBoundingClientRect()
      const range = document.createRange()
      range.selectNodeContents(element)
      const text = range.getBoundingClientRect()
      return Math.min(text.left - brand.left, brand.right - text.right)
    })
    expect(textInset, `park label inset at ${width}px`).toBeGreaterThanOrEqual(8)
    expect(park!.x + park!.width).toBeLessThan(sync!.x)
    expect(sync!.x + sync!.width).toBeLessThanOrEqual(width)
    if (width === 320) await page.screenshot({ path: '/tmp/robopark-header-320-wrap.png' })
  })
}

for (const width of [600, 768, 899] as const) test(`tablet header shows the full mechanic account name at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  const account = page.locator('.rp-shell__topbar .rp-shell__user strong')
  await expect(account).toHaveText('mechanic-e2e')
  const size = await account.evaluate(element => ({ content: element.scrollWidth, visible: element.clientWidth }))
  expect(size.content).toBeLessThanOrEqual(size.visible + 1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  if (width === 768) await page.screenshot({ path: info.outputPath('mechanic-header-768.png'), animations: 'disabled' })
})

for (const width of [320, 390]) test(`owner can read the selected park in the phone system header at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 844 })
  await openRouteFixture(page, 'system', userForRole('royal'))
  const label = page.locator('.rp-shell__topbar .rp-shell__park-brand-name')
  await expect(label).toHaveText('Северный парк')
  expect(await label.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true)
  const park = await page.locator('.rp-shell__topbar .rp-shell__park-brand').boundingBox()
  const sync = await page.locator('.rp-shell__topbar .rp-sync-center').boundingBox()
  expect(park && sync).toBeTruthy()
  expect(park!.x + park!.width).toBeLessThanOrEqual(sync!.x - (width === 320 ? 4 : 8))
  await page.screenshot({ path: `/tmp/robopark-system-owner-phone-header-${width}.png` })
  await page.getByRole('button', { name: 'Сменить парк' }).click()
  await expect(page.getByRole('listbox', { name: 'Сменить парк' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
})

test('route heading keeps a visible focus cue without a full-width outline on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'robot-check', userForRole('mechanic'))
  const heading = page.getByRole('heading', { name: 'Проверка робота', level: 1 })
  await expect(heading).toBeFocused()
  const box = await heading.boundingBox()
  const content = await page.locator('#main-content').boundingBox()
  expect(box && content).toBeTruthy()
  expect(box!.width).toBeLessThan(content!.width * 0.8)
})

for (const theme of ['light', 'dark'] as const) test(`admin bottom navigation keeps every label readable at 320px in ${theme}`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 750 })
  await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
  await openRouteFixture(page, 'admin-robot-check', userForRole('admin'))
  const labels = page.locator('.rp-shell__bottom-nav .rp-shell__nav-label:visible')
  await expect(labels).toHaveCount(5)
  for (const label of await labels.all()) {
    const geometry = await label.evaluate(element => ({
      text: element.textContent,
      width: element.clientWidth,
      scrollWidth: element.scrollWidth,
    }))
    expect(geometry.scrollWidth, geometry.text ?? 'navigation label').toBeLessThanOrEqual(geometry.width)
  }
  await page.screenshot({ path: testInfo.outputPath(`admin-nav-320-${theme}.png`), animations: 'disabled' })
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
    await expect(shell.locator('[data-shell-zone="context"]')).toHaveCount(0)
    await expect(shell.locator('[data-shell-zone="action"]')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    if (width < 900) {
      for (const label of await page.locator('.rp-shell__bottom-nav .rp-shell__nav-label:visible').all()) {
        const geometry = await label.evaluate(element => ({
          fits: element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight,
          text: element.textContent,
          size: `${element.clientWidth}x${element.clientHeight}`,
          scroll: `${element.scrollWidth}x${element.scrollHeight}`,
        }))
        expect(geometry.fits, `${geometry.text}: ${geometry.scroll} in ${geometry.size}`).toBe(true)
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

for (const width of [390, 1440]) for (const theme of ['light', 'dark'] as const) {
  test(`Classic More menu stays a bounded one-column portal at ${width} in ${theme}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ colorScheme: theme, reducedMotion: 'reduce' })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await openRouteFixture(page, 'overview', userForRole('mechanic'))

    const menuLabel = width < 900 ? 'Меню' : 'Ещё'
    await page.getByRole('button', { name: menuLabel, exact: true }).click()
    const dialog = page.getByRole('dialog', { name: menuLabel })
    const controls = dialog.locator('.rp-shell-controls')
    await expect(dialog).toBeVisible()
    await expect(controls).toHaveCount(1)
    await expect(controls).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    const activeLink = controls.locator('.rp-shell__more-link.is-active')
    await expect(activeLink).toHaveCount(1)
    await expect(activeLink).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)')
    await expect(activeLink).toHaveCSS('border-top-color', 'rgba(0, 0, 0, 0)')
    await expect(dialog.locator('.rp-classic-shell')).toHaveCount(0)
    await expect(dialog.getByRole('heading', { name: 'Разделы' })).toBeVisible()
    await expect(dialog.getByRole('heading', { name: 'Профиль' })).toBeVisible()
    const themeOptions = dialog.getByRole('radiogroup', { name: 'Тема оформления' })
    const themeLayout = await themeOptions.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)
    expect(themeLayout).toBe(3)
    const checkedOption = themeOptions.locator('label').filter({ has: page.locator('input:checked') })
    await expect(checkedOption).toHaveCSS('background-color', theme === 'dark' ? 'rgb(48, 62, 43)' : 'rgb(232, 240, 220)')
    await expect(checkedOption.locator('input')).toHaveCSS('appearance', 'none')
    const accentOptions = dialog.getByRole('radiogroup', { name: 'Акцентный цвет' })
    const accentTops = await accentOptions.locator('label').evaluateAll(elements => elements.map(element => element.getBoundingClientRect().top))
    expect(accentTops).toHaveLength(4)
    expect(Math.max(...accentTops) - Math.min(...accentTops)).toBeLessThanOrEqual(1)
    await accentOptions.getByRole('radio', { name: 'Синий' }).click()
    await expect(page.locator('html')).toHaveAttribute('data-accent', 'blue')
    await expect(accentOptions.locator('label').filter({ has: page.locator('input:checked') }))
      .toHaveCSS('background-color', theme === 'dark' ? 'rgb(38, 59, 89)' : 'rgb(231, 238, 252)')
    expect(await page.evaluate(() => localStorage.getItem('robopark-accent'))).toBe('blue')

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
      expect(actions[index].top - actions[index - 1].bottom).toBeGreaterThanOrEqual(8)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await dialog.screenshot({ path: testInfo.outputPath(`classic-more-${width}-${theme}.png`) })
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
