import { expect, test, type Page } from '@playwright/test'
import type { IntegrationSettings } from '../../src/api'
import { installOperational, parkNorth, settlePage } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

test.use({ trace: 'off' })

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
    expect(rect!.width).toBeGreaterThanOrEqual(44)
    expect(rect!.height).toBeGreaterThanOrEqual(44)
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
    { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => ({ json: { operator: true, mechanic: false, driver: false, admin: false, royal: false } }) },
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
    let submitted: { cookie: string; robot_number: string } | undefined
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
          submitted = await request.json() as { cookie: string; robot_number: string }
          return { json: integration('valid') }
        } },
        { method: 'POST', path: '/api/admin/settings/emergency-cookie/check', handler: () => ({ json: integration('valid') }) },
      ],
    })

    await page.goto('/admin/settings')
    await expect(page.getByText('Недействительна')).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expectAdminFits(page)
    const save = page.getByRole('button', { name: 'Сохранить и проверить' })
    await expect(save).toBeDisabled()
    await page.getByLabel('Cookie диагностики робота').fill('candidate-cookie')
    await page.getByLabel('Робот для проверки').fill('447')
    await expect(save).toBeEnabled()
    await save.click()

    await expect.poll(() => submitted).toEqual({ cookie: 'candidate-cookie', robot_number: '447' })
    await expect(page.getByText('Действительна')).toBeVisible()
    await expect(page.getByLabel('Cookie диагностики робота')).toHaveValue('')
    await expectAdminFits(page)
    await assertNoSeriousA11yViolations(page)
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0) })
    await page.mouse.move(0, 0)
    await page.screenshot({ path: info.outputPath(`settings-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
  })
 }
}
