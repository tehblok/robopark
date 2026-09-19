import { expect, test } from '@playwright/test'
import type { DiagnosticRule } from '../../src/api'
import { installOperational } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

const rule: DiagnosticRule = { id: 1, title: 'Лидар', description: 'Проверить питание', part: 'Лидар', source_path: 'errors', match_kind: 'exact', pattern: 'E01', example: 'E01', severity: 'warning', preferred_view: 'front', x: .5, y: .5, indicator: 'point', is_enabled: true, sort_order: 0 }

for (const width of [390, 1440]) test(`sample evaluation stays private and readable at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  let evaluations = 0
  let writes = 0
  page.on('request', request => {
    if (['POST', 'PATCH', 'PUT'].includes(request.method()) && /\/api\/admin\/diagnostic-/.test(request.url()) && !request.url().endsWith('/test-samples')) writes++
  })
  await installOperational(page, { role: 'admin', routes: [
    { method: 'GET', path: '/api/admin/diagnostic-rules', handler: () => ({ json: [rule], headers: { ETag: '"v1"' } }) },
    { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: [] }) },
    { method: 'POST', path: '/api/admin/diagnostic-rules/test-samples', handler: async request => {
      evaluations++
      expect(await request.json()).toMatchObject({ rule: { pattern: 'DRAFT' }, exclude_rule_id: 1, limit: 50 })
      return { json: { items: [
        { id: 11, outcome: 'matched', overlap_rule_ids: Array.from({ length: 40 }, (_, index) => index + 2), reason: null },
        { id: 12, outcome: 'missed', overlap_rule_ids: [], reason: null },
        { id: 13, outcome: 'skipped', overlap_rule_ids: [], reason: 'legacy' },
      ], matched: 1, missed: 1, skipped: 1, overlapping: 1, limit: 50, has_more: true, budget_exhausted: false, invalid_rule_ids: [] } }
    } },
  ] })
  await page.goto('/admin/emergency/config?park=7&tab=indication&rule=1')
  await expect(page.getByLabel('Название ошибки')).toHaveValue('Лидар')
  expect(evaluations).toBe(0)
  await page.getByLabel('Очищенное тело ошибки').fill('DRAFT')
  await page.getByRole('button', { name: 'Проверить собранные ошибки' }).click()
  const result = page.getByRole('region', { name: 'Проверка собранных ошибок' })
  await expect(result.getByRole('status')).toHaveText('Совпало: 1 · Не совпало: 1 · Пропущено: 1')
  await result.getByText('Образцы и пересечения').click()
  await expect(result.getByText('Образец #11')).toBeVisible()
  await expect(result.getByText(/Нет сохранённого оригинала/)).toBeVisible()
  await assertNoSeriousA11yViolations(page)
  const bounds = await result.boundingBox()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  expect(evaluations).toBe(1)
  expect(writes).toBe(0)
  await page.getByLabel('Очищенное тело ошибки').fill('CHANGED')
  await expect(result).toBeHidden()
  await expect(page.getByRole('button', { name: 'Сохранить правило' })).toBeEnabled()
})
