import { expect, test } from '@playwright/test'
import { startDiagnosticApi } from '../support/diagnosticApi'
import { installOperational, snapshot } from './fixtures'
import type { DiagnosticRule, EmergencySnapshot } from '../../src/api'

test.use({ hasTouch: true, trace: 'off' })
const base = '/admin/diagnostic-rules'
const title = 'Передний лидар недоступен'
const description = 'Проверьте питание и соединение переднего лидара.'
const raw = 'LIDAR_OFFLINE'
const part = 'Передний лидар'
const parse = <T>(response: { body?: string; json?: unknown }): T => (response.json ?? JSON.parse(response.body!)) as T

for (const width of [390, 1440]) test(`persisted rule controls real Emergency snapshot at ${width}px`, async ({ page }) => {
  const api = await startDiagnosticApi()
  try {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'admin', routes: api.routes })
    await page.goto('/admin/emergency/config?park=7&tab=indication')
    await page.getByRole('button', { name: 'Новое правило' }).click()
    for (const [label, value] of [['Название ошибки', title], ['Часть робота', part], ['Путь источника', 'errors'], ['Очищенное тело ошибки', raw], ['Расшифровка', description], ['Пример входного значения', raw]]) await page.getByLabel(label, { exact: true }).fill(value)
    await page.getByLabel('Уровень ошибки', { exact: true }).selectOption('critical')
    await page.getByLabel('Ракурс', { exact: true }).selectOption('front')
    await expect(page.getByLabel('Правило включено')).toBeChecked()
    const photo = page.getByRole('img', { name: 'Вид спереди', exact: true })
    await photo.scrollIntoViewIfNeeded()
    await expect.poll(() => photo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true)
    const box = (await photo.boundingBox())!
    if (width < 900) await page.touchscreen.tap(box.x + box.width * .3, box.y + box.height * .6)
    else await page.mouse.click(box.x + box.width * .3, box.y + box.height * .6)
    expect(Number(await page.getByLabel('Координата X').inputValue())).toBeCloseTo(.3, 2)
    expect(Number(await page.getByLabel('Координата Y').inputValue())).toBeCloseTo(.6, 2)
    const previewResponse = page.waitForResponse(response => response.url().endsWith(`${base}/preview`))
    await page.getByRole('button', { name: 'Проверить пример' }).click()
    await expect(page.getByText('Совпадение найдено', { exact: true })).toBeVisible()
    const preview = await (await previewResponse).json()
    expect(preview.events[0]).toMatchObject({ rule_id: 0, title, description, raw_value: raw, part, view: 'front' })
    await page.getByRole('button', { name: 'Сохранить правило' }).click()
    await expect(page).toHaveURL(/rule=\d+/)
    const saved = parse<DiagnosticRule[]>(await api.call({ method: 'GET', path: base }))[0]
    expect(saved).toMatchObject({ title, description, pattern: raw, is_enabled: true, preferred_view: 'front' })
    expect(saved.x).toBeCloseTo(.3, 2); expect(saved.y).toBeCloseTo(.6, 2)
    await page.reload()
    await expect(page.getByLabel('Название ошибки')).toHaveValue(title)

    await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
    await expect(page.getByRole('img', { name: 'Робот: вид спереди' })).toBeVisible()
    await expect(page.locator('.rp-check-event-marker')).toHaveCount(1)
    const marker = page.getByRole('button', { name: `Ошибка: ${title}`, exact: true })
    await marker.tap()
    const details = page.getByRole('region', { name: 'Выбранная ошибка' })
    for (const text of [title, description, part, raw]) await expect(details).toContainText(text)
    const live = parse<EmergencySnapshot>(await api.call({ method: 'GET', path: `/emergency/${snapshot.vin}/snapshot` }))
    expect(live.diagnostic_events!.find(event => event.rule_id === saved.id)).toMatchObject({ title, raw_value: raw, view: 'front', x: saved.x, y: saved.y })
    await page.getByRole('button', { name: /^Все ошибки/ }).click()
    const list = page.getByRole('list', { name: 'Диагностические события' })
    await expect(list).toContainText('UNMAPPED_SENSOR_42'); await expect(list).toContainText('Без локализации')
    await page.getByRole('tab', { name: 'Схема', exact: true }).click()
    await expect(page.locator('.rp-check-wheel-details')).toContainText('Переднее левое колесо')

    const { id: _id, ...second } = saved
    expect((await api.call({ method: 'POST', path: base, body: JSON.stringify({ ...second, title: 'Запасное правило', pattern: 'RESERVE', is_enabled: false }) })).status).toBe(201)
    await page.goto(`/admin/emergency/config?park=7&tab=indication`)
    await page.getByRole('button', { name: `Ниже: ${title}` }).click()
    await expect(page.getByText('Порядок сохранён.')).toBeVisible()
    const ordered = await api.call({ method: 'GET', path: base })
    expect(parse<DiagnosticRule[]>(ordered).map(rule => [rule.title, rule.sort_order])).toEqual([['Запасное правило', 0], [title, 1]])
    await page.getByRole('button', { name: `Открыть правило ${title}` }).click()
    await page.getByRole('button', { name: 'Отключить правило' }).click()
    await expect(page.getByLabel('Правило включено')).not.toBeChecked()
    const stale = await api.call({ method: 'PUT', path: `${base}/reorder`, headers: { 'content-type': 'application/json', 'if-match': ordered.headers!.etag }, body: JSON.stringify({ ids: parse<DiagnosticRule[]>(ordered).map(rule => rule.id) }) })
    expect(stale.status).toBe(409)
    await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
    await expect(page.getByRole('img', { name: 'Робот: вид сверху' })).toBeVisible()
    await expect(page.locator('.rp-check-event-marker')).toHaveCount(0)
    await page.getByRole('button', { name: /^Все ошибки/ }).click()
    await expect(page.getByRole('list', { name: 'Диагностические события' })).toContainText(raw)
    const audit = JSON.stringify(parse(await api.call({ audit: true })))
    expect(audit).toContain('admin.diagnostic_rule.created'); expect(audit).toContain('admin.diagnostic_rule.reordered'); expect(audit).toContain('admin.diagnostic_rule.disabled')
    for (const sensitive of [raw, title, description]) expect(audit).not.toContain(sensitive)
  } finally { await api.close() }
})

for (const actor of ['operator', 'custom-admin', 'admin', 'royal'] as const) test(`${actor} diagnostic access is enforced by real API and page`, async ({ page }) => {
  const api = await startDiagnosticApi(actor)
  try {
    await installOperational(page, { routes: api.routes })
    await page.goto('/admin/emergency/config?park=7&tab=indication')
    const allowed = actor === 'admin' || actor === 'royal'
    const methods = [['GET', base], ['POST', base], ['PATCH', `${base}/1`], ['POST', `${base}/1/disable`], ['PUT', `${base}/reorder`], ['POST', `${base}/preview`]]
    if (allowed) {
      await expect(page.getByRole('button', { name: 'Новое правило' })).toBeVisible()
      expect((await api.call({ method: 'GET', path: base })).status).toBe(200)
      const sentinel = 'PUBLIC_VALIDATION_SENTINEL_42'
      for (const [method, path] of methods.filter(([method]) => method !== 'GET')) {
        const response = await api.call({ method, path, body: JSON.stringify({ [sentinel]: sentinel, x: sentinel, example: sentinel }), headers: { 'content-type': 'application/json' } })
        expect(response.status).toBeGreaterThanOrEqual(400)
        expect(JSON.stringify(response)).not.toContain(sentinel)
      }
      expect(parse(await api.call({ audit: true }))).toEqual([])
    } else {
      await expect(page.getByRole('button', { name: 'Новое правило' })).toHaveCount(0)
      await expect(page.getByRole('tab', { name: 'Ошибки и индикация' })).toHaveCount(0)
      for (const [method, path] of methods) {
        const response = await api.call({ method, path, body: '{}', headers: { 'content-type': 'application/json' } })
        expect(response.status).toBe(403)
      }
    }
  } finally { await api.close() }
})

test('reading thresholds, typed sentinels and keyboard placement persist into real snapshot', async ({ page }) => {
  const api = await startDiagnosticApi()
  try {
    expect((await api.call({ method: 'POST', path: '/admin/emergency/sections', body: JSON.stringify({ id: 'final-readings', title: 'Показания', roles: ['admin'], fields: [] }) })).status).toBe(201)
    const created = await api.call({ method: 'POST', path: '/admin/emergency-readings', body: JSON.stringify({ section_id: 'final-readings', path: 'batteriesStatus.chargePercents', label: 'Заряд тест', display_kind: 'percent', view: 'front', x: .5, y: .5, no_data_values: [null, '0', 0, false] }) })
    expect(created.status).toBe(201)
    await installOperational(page, { role: 'admin', routes: api.routes })
    await page.goto('/admin/emergency/config?tab=readings')
    await page.getByRole('button', { name: 'Открыть показание Заряд тест' }).click()
    await expect(page.getByLabel('Предупреждение ниже')).toHaveCount(0)
    await page.getByRole('button', { name: 'Дополнительные настройки' }).click()
    await page.getByLabel('Предупреждение ниже').fill('90')
    await page.getByLabel('Критично ниже').fill('20')
    await page.getByLabel('Координата X').fill('0.6')
    await page.getByLabel('Координата Y').fill('0.3')
    await page.getByRole('button', { name: 'Сохранить показание' }).click()
    await expect(page.getByText('Показание сохранено.', { exact: true })).toBeVisible()
    const saved = parse<Array<Record<string, unknown>>>(await api.call({ method: 'GET', path: '/admin/emergency-readings' }))[0]
    expect(saved).toMatchObject({ warning_below: 90, critical_below: 20, x: .6, y: .3, no_data_values: [null, '0', 0, false] })
    const live = parse<EmergencySnapshot>(await api.call({ method: 'GET', path: '/emergency/1/snapshot' }))
    expect(live.readings).toEqual([expect.objectContaining({ label: 'Заряд тест', display: '84 %', state: 'warning', x: .6, y: .3 })])
    await page.reload()
    await page.getByRole('button', { name: 'Открыть показание Заряд тест' }).click()
    await page.getByRole('button', { name: 'Дополнительные настройки' }).click()
    await expect(page.getByLabel('Предупреждение ниже')).toHaveValue('90')
    await expect(page.getByLabel('Нет показания')).toHaveValue('null, "0", 0, false')
  } finally { await api.close() }
})
