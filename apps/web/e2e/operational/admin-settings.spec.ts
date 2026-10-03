import { expect, test, type Page } from '@playwright/test'
import type { IntegrationSettings, Park } from '../../src/api'
import { installOperational, issue, operationalRoutes, parkNorth, settlePage, userForRole } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

test.use({ trace: 'off' })

test('owner settings panels use a visible shared section gap', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page, { role: 'royal' })
  await page.goto('/admin/settings?park=7')
  const sections = page.locator('#admin-panel-integrations > .panel')
  await expect(sections.nth(1)).toBeVisible()
  await settlePage(page)
  const first = await sections.nth(0).boundingBox()
  const second = await sections.nth(1).boundingBox()
  expect(first && second).toBeTruthy()
  expect(second!.y - first!.y - first!.height).toBeGreaterThanOrEqual(16)
})

test('all owner settings sections remain discoverable without horizontal scrolling on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { role: 'royal' })
  await page.goto('/admin/settings?park=7')
  const tabs = page.getByRole('tablist', { name: 'Разделы настроек' })
  await expect(tabs).toBeVisible()
  expect(await tabs.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
  for (const tab of await tabs.getByRole('tab').all()) await expect(tab).toBeInViewport()
})

test('park management on a phone has one section navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { role: 'royal' })
  await page.goto('/admin/settings?park=7&tab=parks')

  await expect(page.getByRole('combobox', { name: 'Раздел управления' })).toHaveValue(/tab=parks/)
  await expect(page.getByRole('region', { name: 'Парки' })).toBeVisible()
  await expect(page.getByRole('tablist', { name: 'Разделы настроек' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-parks-navigation-phone.png' })
})

test('settings keep available sections visible after one backend failure on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await installOperational(page, { role: 'admin', routes: [
    { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/settings/integrations', handler: () => ({ json: {
      ...integration('unchecked'), tracker_token_masked: 'configured',
    } }) },
    { method: 'GET', path: '/api/admin/settings/tracker-policy',
      handler: () => ({ status: 503, json: { detail: 'upstream_unavailable' } }) },
    { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => ({ json: {
      operator: false, mechanic: false, driver: false, admin: false, royal: false,
    } }) },
  ] })
  await page.goto('/admin/settings?park=7')

  await expect(page.getByText(/Не удалось загрузить: Политика Tracker/)).toBeVisible()
  await expect(page.getByText('Tracker OAuth', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Защита от скриншотов' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await page.screenshot({ path: '/tmp/robopark-settings-partial-phone.png' })
})

test('settings retain a cached view when reopening after a temporary backend failure', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  let failIntegrations = false
  let integrationReads = 0
  await installOperational(page, { role: 'admin', routes: [
    { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/settings/integrations', handler: () => {
      integrationReads += 1
      return failIntegrations
        ? { status: 503, json: { detail: 'upstream_unavailable' } }
        : { json: { ...integration('unchecked'), tracker_token_masked: 'configured' } }
    } },
    { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: {
      operator_show_untagged: false, operator_show_raw: false,
      operator_show_firmware_profile: false, mechanic_can_write: false,
    } }) },
    { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => ({ json: {
      operator: false, mechanic: false, driver: false, admin: false, royal: false,
    } }) },
  ] })
  await page.goto('/admin/settings?park=7')
  await expect(page.getByText('работает', { exact: true })).toBeVisible()

  failIntegrations = true
  await page.evaluate(() => {
    const currentNow = Date.now
    Date.now = () => currentNow() + 121_000
  })
  await page.getByRole('link', { name: 'Обзор управления' }).click()
  await expect(page).toHaveURL(/\/admin\?park=7/)
  await page.getByRole('link', { name: 'Настройки', exact: true }).click()
  await expect.poll(() => integrationReads).toBeGreaterThan(1)

  await expect(page.getByText('работает', { exact: true })).toBeVisible()
  await expect(page.getByText(/Не удалось загрузить: Интеграции/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440)
  await page.screenshot({ path: '/tmp/robopark-settings-stale-desktop.png' })
})

test('robot-check settings show a service error instead of an empty catalog on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { role: 'royal', routes: [{
    method: 'GET', path: '/api/admin/emergency/sections',
    handler: () => ({ status: 503, json: { detail: 'upstream_unavailable' } }),
  }] })
  await page.goto('/admin/emergency/config?park=7&tab=fields')

  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить разделы')
  await expect(page.getByText('Разделы проверки робота ещё не настроены')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-robot-check-settings-error-phone.png' })
})

test('long park list supports typeahead within the phone viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  const parks = [parkNorth, ...Array.from({ length: 20 }, (_, index) => ({
    ...parkNorth, id: 20 + index, name: `Парк ${index + 1}`,
  })), { ...parkNorth, id: 99, name: 'Next производственный парк с длинным названием' }]
  await installOperational(page, { role: 'admin', parks })
  await page.goto('/robots?park=7')
  const trigger = page.getByRole('button', { name: 'Сменить парк' })
  await trigger.press('ArrowDown')
  const list = page.getByRole('listbox', { name: 'Сменить парк' })
  const box = await list.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(320)
  expect(box!.y + box!.height).toBeLessThanOrEqual(720)
  // Playwright's US keyboard emits key events for Latin characters; Cyrillic
  // prefix matching is covered by the user-event regression.
  await page.keyboard.type('Ne')
  const option = page.getByRole('option', { name: parks.at(-1)!.name })
  await expect(option).toBeFocused()
  await expect(option).toBeInViewport()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/park=99/)
  await expect(trigger).toBeFocused()
})

test('first park accepts its Tracker queue on a 320px phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  let created: Record<string, unknown> | undefined
  let available: Park[] = []
  await installOperational(page, {
    user: { ...userForRole('admin'), parks: [] }, parks: [],
    routes: [
      { method: 'GET', path: '/api/parks', handler: () => ({ status: 200, json: available }) },
      { method: 'POST', path: '/api/parks', handler: async request => {
        created = await request.json() as Record<string, unknown>
        available = [{ id: 7, ...created, is_active: true } as Park]
        return { status: 201, json: available[0] }
      } },
    ],
  })
  await page.goto('/admin/settings?tab=parks')
  await page.getByRole('button', { name: 'Добавить парк' }).click()
  await page.getByLabel('Название').fill('Северный')
  await page.getByLabel('Тег').fill('north')
  await page.getByLabel(/Очередь Tracker/).fill('SDCFLEETOPS')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await page.getByRole('button', { name: 'Создать' }).click()
  await expect.poll(() => created?.tracker_queue).toBe('SDCFLEETOPS')
  await expect(page.locator('.rp-shell__park-brand-name')).toContainText('Северный')
  await page.getByRole('link', { name: 'Обзор' }).last().click()
  await expect(page.getByRole('heading', { name: 'Очередь решений' })).toBeVisible()
})

test('owner can start at an empty overview and reach a working first park', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const user = { ...userForRole('royal'), parks: [] }
  let available: Park[] = []
  const overviewRequests: number[] = []
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/operations/overview') {
      overviewRequests.push(Number(new URL(request.url()).searchParams.get('park_id')))
    }
  })
  await installOperational(page, { user, parks: [], routes: [
    { method: 'GET', path: '/api/parks', handler: () => ({ json: available }) },
    { method: 'POST', path: '/api/parks', handler: async request => {
      const input = await request.json() as Record<string, unknown>
      available = [{ ...parkNorth, ...input, id: 7 } as Park]
      return { status: 201, json: available[0] }
    } },
  ] })
  await page.goto('/overview')
  await page.getByRole('link', { name: 'Создать первый парк' }).click()
  await expect(page).toHaveURL(/\/admin\/settings\?tab=parks/)
  await page.getByRole('button', { name: 'Добавить парк' }).click()
  await page.getByLabel('Название').fill('Первый парк')
  await page.getByLabel('Тег').fill('first')
  await page.getByLabel(/Очередь Tracker/).fill('ROBOPARK')
  await page.getByRole('button', { name: 'Создать' }).click()
  await expect(page.locator('.rp-shell__park-brand-name')).toContainText('Первый парк')
  await page.getByRole('link', { name: 'Обзор' }).last().click()
  await expect(page.getByRole('region', { name: 'Первый парк' }).getByRole('heading', { name: 'Очередь решений' })).toBeVisible()
  await expect.poll(() => overviewRequests).toContain(7)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-first-park-owner-phone.png', fullPage: true })
})

test('owner reaches Tracker integration setup when the new park has no token', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const oneTimeTestToken = crypto.randomUUID()
  let saved = false
  const readyOverview = operationalRoutes({ user: userForRole('royal') }).find(
    route => route.method === 'GET' && route.path === '/api/operations/overview',
  )!
  await installOperational(page, { role: 'royal', routes: [
    { method: 'GET', path: '/api/operations/overview', handler: request => saved
      ? readyOverview.handler(request)
      : { status: 503, json: { detail: 'tracker_token_not_configured' } } },
    { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/settings/integrations', handler: () => ({ json: integration('unchecked') }) },
    { method: 'PUT', path: '/api/admin/settings/tracker-token', handler: async request => {
      saved = (await request.json() as { token: string }).token === oneTimeTestToken
      return { json: { ...integration('unchecked'), tracker_token_masked: 'set' } }
    } },
    { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: {
      operator_show_untagged: false, operator_show_raw: false,
      operator_show_firmware_profile: false, mechanic_can_write: false,
    } }) },
    { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => ({ json: {
      operator: false, mechanic: false, driver: false, admin: false, royal: false,
    } }) },
    { method: 'GET', path: '/api/admin/settings/registration-password', handler: () => ({ json: {
      configured: false, password_masked: null, updated_at: null,
    } }) },
  ] })
  await page.goto('/overview?park=7')
  const setup = page.getByRole('link', { name: 'Настроить Tracker' })
  await expect(setup).toBeVisible()
  await page.screenshot({ path: '/tmp/robopark-first-run-tracker-setup-phone.png' })
  await setup.click()
  await expect(page).toHaveURL(/\/admin\/settings\?park=7&tab=integrations#tracker-token/)
  await expect(page.getByRole('tab', { name: 'Интеграции' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByLabel('Tracker OAuth-токен')).toBeInViewport()
  await page.screenshot({ path: '/tmp/robopark-first-run-tracker-field-phone.png' })
  await page.getByLabel('Tracker OAuth-токен').fill(oneTimeTestToken)
  await page.getByRole('button', { name: 'Сохранить токен' }).click()
  await expect(page.getByText('Токен Tracker сохранён')).toBeVisible()
  await expect(page.getByLabel('Tracker OAuth-токен')).toHaveValue('')
  expect(saved).toBe(true)
  await page.getByRole('link', { name: 'Обзор' }).last().click()
  const firstParkTask = page.getByRole('region', { name: parkNorth.name })
    .getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })
  await expect(firstParkTask).toBeVisible()
  await expect(firstParkTask).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
  await firstParkTask.click()
  await expect(page).toHaveURL(/\/work\/ROBOPARK-42\?park=7/)
  await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Задача', exact: true })).toHaveAttribute('aria-selected', 'true')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-first-run-work-phone.png', fullPage: true })
})

async function expectAdminFits(page: Page) {
  await settlePage(page)
  // Measure rendered content, not scrollWidth (which includes reserved gutters).
  const overflow = await page.getByRole('main').evaluate((main) => {
    const bounds = main.getBoundingClientRect()
    return Array.from(main.querySelectorAll('.panel, .field, input, button, .btn, .toggle, .stat'))
      .filter((element) => element.getClientRects().length)
      .flatMap((element) => {
        const rect = element.getBoundingClientRect()
        const panel = element.closest('.panel')?.getBoundingClientRect() ?? bounds
        const left = Math.max(0, bounds.left, panel.left)
        const right = Math.min(innerWidth, bounds.right, panel.right)
        return rect.left < left - 1 || rect.right > right + 1
          ? [{ element: element.tagName, className: element.className, left: rect.left, right: rect.right, available: { left, right } }]
          : []
      })
  })
  expect(overflow).toEqual([])
  for (const control of await page.getByRole('main').locator('input:not([type="checkbox"]), button, .btn, .toggle').all()) {
    await control.scrollIntoViewIfNeeded()
    await expect(control).toBeVisible()
    const rect = await control.boundingBox()
    expect(rect).not.toBeNull()
    // Firefox can report a nominal 44 CSS px as 43.999969 after transforms.
    expect(rect!.width).toBeGreaterThanOrEqual(44 - 0.001)
    expect(rect!.height).toBeGreaterThanOrEqual(44 - 0.001)
    expect(rect!.y).toBeGreaterThanOrEqual(-1)
    expect(rect!.y + rect!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1)
  }
}

function integration(status: IntegrationSettings['emergency_cookie_status']): IntegrationSettings {
  return {
    tracker_token_masked: null,
    tracker_token_updated_at: null,
    emergency_cookie_masked: null,
    emergency_cookie_updated_at: null,
    emergency_cookie_encrypted: false,
    emergency_cookie_valid: status === 'valid' ? true : status === 'invalid' ? false : null,
    emergency_cookie_status: status,
    emergency_cookie_checked_at: status === 'unchecked' ? null : '2026-09-06T09:00:00Z',
    emergency_cookie_checked_robot: status === 'unchecked' ? null : '447',
  }
}

test('cold integration success does not invent editable screenshot protection', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  let recovered = false
  let mutations = 0
  await installOperational(page, { role: 'admin', routes: [
    { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/settings/integrations', handler: () => recovered ? { json: integration('valid') } : { status: 503, json: { detail: 'bootstrap_offline' } } },
    { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: { operator_show_untagged: false, operator_show_raw: false, operator_show_firmware_profile: false, mechanic_can_write: false } }) },
    { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => recovered
      ? { json: { operator: true, mechanic: false, driver: false, admin: false, royal: false } }
      : { status: 503, json: { detail: 'guard_unavailable' } } },
    { method: 'PUT', path: '/api/admin/settings/screenshot-guard', handler: () => { mutations += 1; return { status: 500 } } },
    { method: 'POST', path: '/api/admin/settings/emergency-cookie/check', handler: () => ({ json: integration('valid') }) },
  ] })
  await page.goto('/admin/settings?park=7')
  await page.getByRole('button', { name: 'Проверить текущую', exact: true }).click()
  await expect(page.getByText('Действительна', { exact: true })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Состояние защиты не загружено' })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /Запрет скриншотов/ })).toHaveCount(0)
  expect(mutations).toBe(0)
  await expectAdminFits(page)
  await assertNoSeriousA11yViolations(page)
  await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0) })
  await page.mouse.move(0, 0)
  await page.screenshot({ path: info.outputPath('settings-partial-light-390.png'), fullPage: true, animations: 'disabled' })
  recovered = true
  await page.getByRole('button', { name: 'Повторить загрузку настроек' }).click()
  await expect(page.getByRole('checkbox', { name: 'Запрет скриншотов — Оператор', exact: true })).toBeChecked()
  await expect(page.getByText('Действительна', { exact: true })).toBeVisible()
})

for (const theme of ['light', 'dark'] as const) {
 for (const width of [320, 390, 768, 1024, 1440]) {
  test(`admin validates a replacement robot-check cookie at ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 720 })
    await page.addInitScript((preference) => localStorage.setItem('robopark-theme', preference), theme)
    let submitted: { cookie: string } | undefined
    let checked: { robot_number: string } | undefined
    await installOperational(page, {
      role: 'admin',
      routes: [
        { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
        { method: 'GET', path: '/api/admin/settings/integrations', handler: () => ({ json: integration('invalid') }) },
        { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: {
          operator_show_untagged: false, operator_show_raw: false,
          operator_show_firmware_profile: false, mechanic_can_write: false,
        } }) },
        { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => ({ json: {
          operator: false, mechanic: false, admin: false, royal: false, driver: false,
        } }) },
        { method: 'PUT', path: '/api/admin/settings/emergency-cookie', handler: async (request) => {
          submitted = await request.json() as { cookie: string }
          return { json: integration('valid') }
        } },
        { method: 'POST', path: '/api/admin/settings/emergency-cookie/check', handler: async request => {
          checked = await request.json() as { robot_number: string }
          return { json: integration('valid') }
        } },
      ],
    })

    await page.goto('/admin/settings')
    await expect(page.getByText('Недействительна')).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expectAdminFits(page)
    const save = page.getByRole('button', { name: 'Сохранить cookie' })
    await expect(save).toBeDisabled()
    await page.getByLabel('Cookie диагностики робота').fill('candidate-cookie')
    await page.getByLabel('Робот для проверки').fill('447')
    await expect(save).toBeEnabled()
    await save.click()

    await expect.poll(() => submitted).toEqual({ cookie: 'candidate-cookie' })
    await expect(page.getByText('Действительна')).toBeVisible()
    await expect(page.getByLabel('Cookie диагностики робота')).toHaveValue('')
    await page.getByRole('button', { name: 'Проверить текущую' }).click()
    await expect.poll(() => checked).toEqual({ robot_number: '447' })
    await expectAdminFits(page)
    await assertNoSeriousA11yViolations(page)
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0) })
    await page.mouse.move(0, 0)
    await page.screenshot({ path: info.outputPath(`settings-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
  })
 }
}
