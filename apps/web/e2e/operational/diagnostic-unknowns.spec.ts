import { expect, test, type Page } from '@playwright/test'
import type { DiagnosticRule } from '../../src/api'
import type { UnknownDiagnostic } from '../../src/domains/diagnostics/unknownDiagnosticApi'
import { installOperational, settlePage } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

test.use({ trace: 'off' })
const unknown: UnknownDiagnostic = { id: 7, source_path: 'errors.0', raw_value: 'DRIVE_OFFLINE', original_value: 'DRIVE_OFFLINE', pattern: 'DRIVE_OFFLINE', first_seen_at: '2026-09-01T09:00:00Z', last_seen_at: '2026-09-06T09:00:00Z', observations: 19, last_robot: '447', state: 'new', rule_id: null }
async function geometry(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  const outside = await page.locator('.rp-diagnostic-editor').evaluate(editor => Array.from(editor.querySelectorAll('input,select,textarea,button,figure,pre')).filter(element => element.getClientRects().length).flatMap(element => {
    const rect = element.getBoundingClientRect()
    return rect.left < -1 || rect.right > innerWidth + 1 ? [{ tag: element.tagName, text: element.textContent?.slice(0, 50), left: rect.left, right: rect.right }] : []
  }))
  expect(outside).toEqual([])
  for (const control of await page.locator('.rp-diagnostic-editor button:visible,.rp-diagnostic-editor input:not([type="checkbox"]):visible,.rp-diagnostic-editor select:visible,.rp-diagnostic-editor textarea:visible').all()) {
    const box = (await control.boundingBox())!
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)
  }
}
for (const theme of ['light', 'dark'] as const) test(`admin classifies a grouped unknown through preview and opens its rule at 320px ${theme}`, async ({ page }, info) => {
  let items = [{ ...unknown }]
  const rules: DiagnosticRule[] = []
  let classifications = 0
  await page.setViewportSize({ width: 320, height: 900 })
  await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
  await installOperational(page, { role: 'admin', routes: [
    { method: 'GET', path: '/api/admin/diagnostic-rules', handler: () => ({ json: rules, headers: { ETag: '"v1"' } }) },
    { method: 'GET', path: '/api/admin/diagnostic-unknowns', handler: request => {
      const matches = items.filter(item => item.state === new URL(request.url).searchParams.get('state'))
      return { json: { items: matches, total: matches.length, limit: 50, offset: 0, has_more: false } }
    } },
    { method: 'POST', path: '/api/admin/diagnostic-rules/preview', handler: async request => {
      const candidate = (await request.json()).rule
      expect(candidate).toMatchObject({ source_path: 'errors.0', pattern: 'DRIVE_OFFLINE', example: '"DRIVE_OFFLINE"', preferred_view: 'rear', x: .3, y: .7 })
      return { json: { matched: true, events: [{ ...candidate, id: 'candidate', rule_id: 0, raw_value: unknown.raw_value, source_segments: ['errors', 0], view: 'rear', sort_order: 0 }] } }
    } },
    { method: 'POST', path: '/api/admin/diagnostic-unknowns/7/classify', handler: async request => {
      classifications++
      const candidate = (await request.json()).rule
      const created: DiagnosticRule = { ...candidate, id: 3, sort_order: 0 }
      rules.push(created); items = [{ ...unknown, state: 'mapped', rule_id: 3 }]
      return { status: 201, json: created }
    } },
    { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: [] }) },
  ] })
  await page.goto('/admin/emergency/config?park=7&tab=indication')
  await page.getByRole('tab', { name: 'Неизвестные ошибки', exact: true }).click()
  const inbox = page.getByRole('tabpanel', { name: 'Неизвестные ошибки', exact: true })
  await expect(inbox.getByRole('button', { name: /Наблюдений: 19/ })).toBeVisible()
  await settlePage(page); await geometry(page)
  await page.screenshot({ path: info.outputPath(`unknown-list-${theme}-320.png`), fullPage: true })
  await inbox.getByRole('button', { name: /Наблюдений: 19/ }).click()
  await inbox.getByRole('button', { name: 'Разметить', exact: true }).click()
  await expect(inbox.getByLabel('Координата X')).toBeEmpty()
  await expect(inbox.getByRole('button', { name: 'Сохранить правило' })).toBeDisabled()
  for (const [label, value] of [['Название ошибки', 'Неисправность привода'], ['Часть робота', 'Привод'], ['Расшифровка', 'Проверьте питание и соединение привода.'], ['Координата X', '.3'], ['Координата Y', '.7']]) await inbox.getByLabel(label, { exact: true }).fill(value)
  await inbox.getByLabel('Ракурс', { exact: true }).selectOption('rear')
  await inbox.getByRole('button', { name: 'Проверить пример' }).click()
  await expect(inbox.getByText('Совпадение найдено', { exact: true })).toBeVisible()
  await geometry(page); await assertNoSeriousA11yViolations(page)
  await page.screenshot({ path: info.outputPath(`unknown-preview-${theme}-320.png`), fullPage: true })
  await inbox.getByRole('button', { name: 'Сохранить правило' }).click()
  await expect(page).toHaveURL(/rule=3/)
  const catalog = page.getByRole('tabpanel', { name: 'Каталог ошибок', exact: true })
  await expect(catalog.getByLabel('Название ошибки')).toHaveValue('Неисправность привода')
  await expect(catalog.getByLabel('Координата Y')).toHaveValue('0.7')
  await page.getByRole('tab', { name: 'Неизвестные ошибки', exact: true }).click()
  await inbox.getByRole('button', { name: 'Назад к списку' }).click()
  await inbox.getByRole('tab', { name: 'Размеченные', exact: true }).click()
  await inbox.getByRole('button', { name: /Наблюдений: 19/ }).click()
  await inbox.getByRole('button', { name: 'Открыть правило №3' }).click()
  await expect(catalog.getByLabel('Название ошибки')).toHaveValue('Неисправность привода')
  await geometry(page)
  expect(classifications).toBe(1)
})
