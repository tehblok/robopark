import { expect, test } from '@playwright/test'
import type { IntegrationSettings } from '../../src/api'
import { installOperational } from './fixtures'

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

for (const width of [390, 1440]) {
  test(`admin validates a replacement robot-check cookie at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 720 })
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
    const save = page.getByRole('button', { name: 'Сохранить и проверить' })
    await expect(save).toBeDisabled()
    await page.getByLabel('Cookie диагностики робота').fill('candidate-cookie')
    await page.getByLabel('Робот для проверки').fill('447')
    await expect(save).toBeEnabled()
    await save.click()

    await expect.poll(() => submitted).toEqual({ cookie: 'candidate-cookie', robot_number: '447' })
    await expect(page.getByText('Действительна')).toBeVisible()
    await expect(page.getByLabel('Cookie диагностики робота')).toHaveValue('')
    const secretPanel = page.getByRole('heading', { name: 'Секреты' })
      .locator('xpath=ancestor::section')
    const contentRight = await page.locator('.rp-shell__content').evaluate(
      (element) => element.getBoundingClientRect().right,
    )
    const controlsFit = await secretPanel.locator('input, button').evaluateAll(
      (controls, right) => controls.every((control) => control.getBoundingClientRect().right <= right + 1),
      contentRight,
    )
    expect(controlsFit).toBe(true)
  })
}
