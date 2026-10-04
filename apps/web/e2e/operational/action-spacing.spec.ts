import { expect, test, type Page } from '@playwright/test'
import { openRouteFixture } from './routeFixtures'
import { parkNorth, roles, settlePage, userForRole } from './fixtures'
import { installMockApi } from '../support/mockApi'
import { canAccessRoute } from '../../src/app/routing/accessPolicy'
import { ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'

const screens = [
  'overview', 'operator-parks', 'work', 'work-issue', 'robots', 'robot-detail', 'robot-check',
  'legacy-robot-check', 'inventory', 'reports', 'reports-new', 'report-detail', 'campaigns', 'campaign-detail',
  'schedule', 'analytics', 'system', 'admin', 'admin-users', 'admin-roles', 'admin-settings',
  'admin-robot-check',
] as const

const roleFor = (route: typeof screens[number]) => route === 'operator-parks' || route === 'reports' || route === 'report-detail'
  ? 'operator' as const
  : route === 'schedule'
    ? 'driver' as const
    : route === 'work' || route === 'work-issue' || route === 'robots' || route === 'robot-detail' || route === 'robot-check' || route === 'legacy-robot-check' || route === 'inventory'
      ? 'mechanic' as const
      : 'royal' as const

test.describe.configure({ mode: 'parallel' })

for (const width of [390, 1440]) test(`reading catalog separates its heading actions from the discovery panel at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await openRouteFixture(page, 'admin-robot-check', userForRole('royal'))
  await page.getByRole('tab', { name: 'Показания', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Каталог показаний', exact: true })).toBeVisible()
  const heading = await page.locator('.rp-diagnostic-list-heading').boundingBox()
  const panel = await page.getByRole('region', { name: 'Поиск поля в примере робота' }).boundingBox()
  expect(heading && panel).toBeTruthy()
  expect(panel!.y - (heading!.y + heading!.height)).toBeGreaterThanOrEqual(8)
  await page.screenshot({ path: `/tmp/robopark-reading-layout-${width}.png`, animations: 'disabled' })
})

async function expectSeparatedActions(page: Page, scope = 'body') {
  const touching = await page.locator(scope).evaluate(body => {
    const visibleBounds = (element: HTMLElement) => {
      const box = element.getBoundingClientRect()
      const bounds = { left: box.left, right: box.right, top: box.top, bottom: box.bottom }
      for (let parent = element.parentElement; parent && parent !== body; parent = parent.parentElement) {
        const style = getComputedStyle(parent)
        const clip = parent.getBoundingClientRect()
        if (/auto|scroll|hidden|clip/.test(style.overflowX)) {
          bounds.left = Math.max(bounds.left, clip.left)
          bounds.right = Math.min(bounds.right, clip.right)
        }
        if (/auto|scroll|hidden|clip/.test(style.overflowY)) {
          bounds.top = Math.max(bounds.top, clip.top)
          bounds.bottom = Math.min(bounds.bottom, clip.bottom)
        }
      }
      return bounds
    }
    const actions = Array.from(body.querySelectorAll<HTMLElement>('button, a[href], [role="button"]'))
      .map(element => ({ element, box: visibleBounds(element) }))
      .filter(({ element, box }) => {
        const style = getComputedStyle(element)
        return box.right - box.left > 1 && box.bottom - box.top > 1 && style.visibility !== 'hidden' && style.display !== 'none'
          && !element.matches('.rp-shell__skip-link:not(:focus)')
          && !element.closest('.leaflet-bar')
      })
    const failures: string[] = []
    for (let index = 0; index < actions.length; index++) for (const next of actions.slice(index + 1)) {
      const firstAction = actions[index].element
      const secondAction = next.element
      if (firstAction.contains(secondAction) || secondAction.contains(firstAction)) continue
      const firstInBottomNav = Boolean(firstAction.closest('.rp-shell__bottom-nav'))
      const secondInBottomNav = Boolean(secondAction.closest('.rp-shell__bottom-nav'))
      if (firstInBottomNav !== secondInBottomNav) continue
      const firstInTopbar = Boolean(firstAction.closest('.rp-shell__topbar'))
      const secondInTopbar = Boolean(secondAction.closest('.rp-shell__topbar'))
      if (firstInTopbar !== secondInTopbar) continue
      const first = actions[index].box
      const second = next.box
      const overlapX = Math.min(first.right, second.right) - Math.max(first.left, second.left)
      const overlapY = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top)
      const gapX = first.right <= second.left ? second.left - first.right : second.right <= first.left ? first.left - second.right : -1
      const gapY = first.bottom <= second.top ? second.top - first.bottom : second.bottom <= first.top ? first.top - second.bottom : -1
      if ((overlapX > 1 && overlapY > 1)
        || (overlapX > 8 && gapY >= 0 && gapY < 8)
        || (overlapY > 8 && gapX >= 0 && gapX < 8)) {
        failures.push(`${firstAction.className}: ${firstAction.textContent?.trim()} / ${secondAction.textContent?.trim()} (${Math.max(gapX, gapY)}px)`)
      }
    }
    // A padded action can look separated while its surrounding card touches
    // the next action group. Check the visible frames as well as the controls.
    for (const surface of body.querySelectorAll<HTMLElement>('.rp-panel, .panel, .card, .issue-actions, .rp-responsive-disclosure')) {
      const sibling = surface.nextElementSibling
      if (!(sibling instanceof HTMLElement) || !surface.querySelector('button, a[href]') || !sibling.querySelector('button, a[href]')) continue
      const style = getComputedStyle(surface)
      if (style.display === 'none' || style.visibility === 'hidden') continue
      const first = visibleBounds(surface)
      const second = visibleBounds(sibling)
      if (first.right - first.left <= 1 || first.bottom - first.top <= 1 || second.right - second.left <= 1 || second.bottom - second.top <= 1) continue
      const overlapX = Math.min(first.right, second.right) - Math.max(first.left, second.left)
      const overlapY = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top)
      const gapX = second.left - first.right
      const gapY = second.top - first.bottom
      if ((overlapX > 8 && gapY >= 0 && gapY < 8) || (overlapY > 8 && gapX >= 0 && gapX < 8)) {
        failures.push(`surface ${surface.className} / ${sibling.className} (${Math.max(gapX, gapY)}px)`)
      }
    }
    return failures.slice(0, 20)
  })
  expect(touching).toEqual([])
}

for (const width of [320, 390, 768, 1280, 1440, 1920] as const) for (const route of screens) test(`${route} keeps a visible gap between all action controls at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 })
  if (width === 390) await page.addInitScript(() => localStorage.setItem('robopark-theme', 'dark'))
  await openRouteFixture(page, route, userForRole(roleFor(route)))
  await settlePage(page)
  await expectSeparatedActions(page)
  if (width === 320) await page.screenshot({ path: info.outputPath(`${route}-actions-320.png`), animations: 'disabled' })
  if (width === 390) await page.screenshot({ path: info.outputPath(`${route}-actions-dark-390.png`), animations: 'disabled' })
  if ((width === 768 || width === 1280 || width === 1920) && ['overview', 'work-issue', 'system'].includes(route)) {
    await page.screenshot({ path: info.outputPath(`${route}-actions-${width}.png`), animations: 'disabled' })
  }
  if (route === 'work-issue' && width <= 390) {
    await page.screenshot({ path: info.outputPath(`work-issue-actions-${width}.png`), fullPage: true, animations: 'disabled' })
  }
})

for (const width of [320, 390] as const) test(`work issue expanded actions remain separated at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 })
  await openRouteFixture(page, 'work-issue', userForRole('mechanic'))
  await settlePage(page)
  await page.getByRole('button', { name: 'Передать на проверку' }).click()
  await expect(page.locator('form:has(select[aria-label="Что случилось?"])')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Передать на проверку' })).toHaveCount(1)
  await expectSeparatedActions(page)
  if (width === 390) await page.screenshot({ path: info.outputPath('work-issue-review-open-390.png'), fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: 'Отмена' }).click()
  await expect(page.locator('form:has(select[aria-label="Что случилось?"])')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Передать на проверку' })).toHaveCount(1)
})

for (const width of [390, 1440] as const) for (const role of roles) for (const route of ['overview', 'work-issue', 'robot-detail', 'robot-check', 'reports', 'inventory', 'schedule', 'system'] as const) {
  test(`${role} ${route} keeps its role-specific actions separated at ${width}px`, async ({ page }) => {
    const user = userForRole(role)
    test.skip(!canAccessRoute(user, route), 'route denied by access policy')
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, route, user)
    await settlePage(page)
    await expectSeparatedActions(page)
  })
}

const accessScreens = [
  ['login', '/login'], ['register', '/register'], ['change-password', '/change-password'],
  ['no-cabinet', '/no-cabinet'], ['access-pending', '/access/pending'],
  ['access-rejected', '/access/rejected'], ['mechanic-no-park', '/mechanic/no-park'],
] as const

test('action spacing covers every canonical screen with its own layout', () => {
  const covered = new Set<string>([...screens, ...accessScreens.map(([route]) => route)])
  const redirectsWithoutOwnLayout = new Set(['home', 'admin-tracker', 'not-found'])
  expect(ROUTE_MANIFEST.filter(route => !route.redirectTo && !covered.has(route.id) && !redirectsWithoutOwnLayout.has(route.id)).map(route => route.id)).toEqual([])
})

test('320px header keeps its visible park and sync frames apart', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await openRouteFixture(page, 'overview', userForRole('royal'))
  const park = await page.locator('.rp-shell__topbar .rp-shell__park-brand').boundingBox()
  const sync = await page.locator('.rp-shell__topbar .rp-sync-center__trigger').boundingBox()
  expect(park && sync).toBeTruthy()
  expect(sync!.x - (park!.x + park!.width)).toBeGreaterThanOrEqual(8)
})

for (const width of [320, 390, 768, 1280, 1440, 1920] as const) for (const [route, path] of accessScreens) test(`${route} keeps a visible gap between all action controls at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  const base = userForRole(route === 'mechanic-no-park' ? 'mechanic' : 'operator')
  const user = route === 'login' || route === 'register' ? null
    : route === 'change-password' ? { ...base, must_change_password: true }
      : route === 'no-cabinet' ? { ...base, role: 'royal' as const, permissions: [] }
        : route === 'access-pending' ? { ...base, access_status: 'pending' as const }
          : route === 'access-rejected' ? { ...base, access_status: 'rejected' as const }
            : route === 'mechanic-no-park' ? { ...base, parks: [] }
              : base
  await installMockApi(page, { user, parks: user?.parks ?? [parkNorth] })
  await page.goto(path)
  await expect(page.locator('main')).toBeVisible()
  await settlePage(page)
  expect(new URL(page.url()).pathname).toBe(path)
  await expectSeparatedActions(page)
})

for (const width of [320, 390, 1440] as const) test(`robot-check More menu separates its actions at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 })
  await openRouteFixture(page, 'robot-detail', userForRole('mechanic'))
  await page.locator('.rp-check-more > button').click()
  const menu = page.getByRole('menu', { name: 'Другие разделы' })
  await expect(menu).toBeVisible()
  const gaps = await menu.locator('button').evaluateAll(buttons => buttons.slice(1).map((button, index) => (
    button.getBoundingClientRect().top - buttons[index].getBoundingClientRect().bottom
  )))
  expect(gaps.length).toBeGreaterThan(0)
  for (const gap of gaps) expect(gap).toBeGreaterThanOrEqual(8)
  if (width === 320) await page.screenshot({ path: info.outputPath('robot-check-more-320.png'), animations: 'disabled' })
})

for (const width of [320, 390] as const) for (const theme of ['light', 'dark'] as const) {
  test(`mobile More menu has separated actions and no active color block at ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(next => localStorage.setItem('robopark-theme', next), theme)
    await openRouteFixture(page, 'system', userForRole('royal'))
    await page.getByRole('button', { name: 'Меню' }).click()
    const menu = page.getByRole('dialog', { name: 'Меню' })
    await expect(menu).toBeVisible()
    const active = menu.locator('.rp-shell__more-link.is-active')
    await expect(active).toHaveCount(1)
    expect(await active.evaluate(element => getComputedStyle(element).backgroundColor)).toBe('rgba(0, 0, 0, 0)')
    const gaps = await menu.locator('.rp-shell__more-nav').evaluateAll(navs => navs.flatMap(nav => {
      const links = Array.from(nav.querySelectorAll<HTMLElement>('.rp-shell__more-link'))
      return links.slice(1).map((link, index) => link.getBoundingClientRect().top - links[index].getBoundingClientRect().bottom)
    }))
    expect(gaps.length).toBeGreaterThan(0)
    for (const gap of gaps) expect(gap).toBeGreaterThanOrEqual(8)
    if (width === 320) await menu.screenshot({ path: info.outputPath(`more-menu-${theme}-320.png`), animations: 'disabled' })
  })
}

const expandedScreens = [
  { route: 'robots', role: 'mechanic', action: 'button:has-text("Сканировать")', ready: '[role="dialog"]:has-text("Сканировать робота")', scope: '.rp-dialog' },
  { route: 'inventory', role: 'mechanic', tab: 'Поставки', action: 'button:has-text("Новая поставка")', ready: '#receipt-supplier', scope: 'body' },
  { route: 'campaigns', role: 'royal', before: 'button:has-text("Новая кампания")', action: 'button[aria-haspopup="dialog"]:has-text("Парки кампании")', ready: '[role="dialog"][aria-label="Парки кампании"]', scope: '[role="dialog"][aria-label="Парки кампании"]' },
  { route: 'admin-users', role: 'royal', before: 'button:has-text("Назад к списку")', action: 'button[aria-label="Открыть аккаунт route-admin"]', ready: 'section[aria-label="Опасные действия"]', scope: 'body' },
  { route: 'admin-robot-check', role: 'royal', tab: 'Показания', action: 'button:has-text("Новое показание")', ready: 'section[aria-label="Предпросмотр показания"]', scope: 'body' },
] as const

for (const width of [320, 390] as const) for (const scenario of expandedScreens) {
  test(`${scenario.route} expanded actions stay separate at ${width}px`, async ({ page }, info) => {
    test.setTimeout(12_000)
    await page.setViewportSize({ width, height: 900 })
    await openRouteFixture(page, scenario.route, userForRole(scenario.role))
    if ('tab' in scenario) {
      if (scenario.route === 'inventory') await page.getByRole('combobox', { name: 'Раздел склада' }).selectOption('receipts')
      else await page.getByRole('tab', { name: scenario.tab, exact: true }).click()
    }
    if ('before' in scenario) await page.locator(scenario.before).first().click()
    await page.locator(scenario.action).first().click()
    await expect(page.locator(scenario.ready)).toBeVisible()
    if (scenario.route === 'robots') await expect(page.locator('.rp-robot-scanner video')).toBeHidden()
    await expectSeparatedActions(page, scenario.scope)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
    if (scenario.route === 'campaigns') {
      const popup = await page.locator(scenario.ready).boundingBox()
      const navigation = await page.locator('.rp-shell__bottom-nav').boundingBox()
      expect(popup && navigation).toBeTruthy()
      expect(popup!.y + popup!.height).toBeLessThanOrEqual(navigation!.y - 8)
    }
    if (width === 320) await page.screenshot({ path: info.outputPath(`${scenario.route}-expanded-320.png`), fullPage: scenario.route !== 'campaigns', animations: 'disabled' })
  })
}

for (const width of [320, 390] as const) test(`mobile account detail opens from its heading at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await openRouteFixture(page, 'admin-users', userForRole('royal'))
  await expect(page.getByRole('heading', { name: 'route-admin', exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.scrollY)).toBeLessThanOrEqual(8)
  await expect(page.locator('.admin-users .rp-management-metrics')).toBeHidden()
  const activity = page.locator('.rp-user-activity')
  await expect(activity).not.toHaveAttribute('open')
  const role = await page.locator('.rp-master-detail__detail select').first().boundingBox()
  const navigation = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(role && navigation).toBeTruthy()
  expect(role!.y + role!.height).toBeLessThan(navigation!.y)
  await activity.locator('summary').click()
  await expect(activity).toHaveAttribute('open')
  await page.getByRole('button', { name: 'Назад к списку' }).click()
  await expect(page.locator('.admin-users .rp-management-metrics')).toBeVisible()
})
