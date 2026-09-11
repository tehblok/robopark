import { expect, test, type Page } from '@playwright/test'
import type { DiagnosticRule } from '../../src/api'
import { installOperational, settlePage } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

test.use({ trace: 'off', hasTouch: true })
const rule: DiagnosticRule = { id: 1, title: 'Неисправность переднего лидара', description: 'Проверьте питание и соединение переднего лидара.', part: 'Передний лидар', source_path: 'errors', match_kind: 'exact', pattern: 'LIDAR_OFFLINE', example: 'LIDAR_OFFLINE', severity: 'critical', preferred_view: 'front', x: .25, y: .6, indicator: 'point', is_enabled: true, sort_order: 0 }
async function installEditor(page: Page, pending: { create?: () => Promise<void>; preview?: () => Promise<void> } = {}) {
  let rules = [rule, { ...rule, id: 2, title: 'Перегрев батареи', part: 'Батарея', pattern: 'BATTERY_HOT', severity: 'warning' as const, preferred_view: 'rear' as const, is_enabled: false, sort_order: 1 }]
  let revision = 1
  const etag = () => `"v${revision}"`
  await installOperational(page, { role: 'admin', routes: [
    { method: 'GET', path: '/api/admin/diagnostic-rules', handler: () => ({ json: rules, headers: { ETag: etag() } }) },
    { method: 'POST', path: '/api/admin/diagnostic-rules', handler: async request => { const created = { ...await request.json(), id: 3 } as DiagnosticRule; await pending.create?.(); rules = [...rules, created]; revision++; return { status: 201, json: created } } },
    { method: 'PATCH', path: /\/api\/admin\/diagnostic-rules\/\d+$/, handler: async request => {
      const body = await request.json(); expect(body).not.toHaveProperty('sort_order'); expect(body).not.toHaveProperty('id')
      const id = Number(new URL(request.url).pathname.split('/').at(-1)); rules = rules.map(item => item.id === id ? { ...item, ...body } : item); revision++
      return { json: rules.find(item => item.id === id) }
    } },
    { method: 'POST', path: '/api/admin/diagnostic-rules/1/disable', handler: () => { rules = rules.map(item => item.id === 1 ? { ...item, is_enabled: false } : item); revision++; return { json: rules[0] } } },
    { method: 'PUT', path: '/api/admin/diagnostic-rules/reorder', handler: async request => {
      expect(request.headers.get('If-Match')).toBe(etag())
      const body = await request.json() as { ids: number[] }; expect([...body.ids].sort()).toEqual(rules.map(item => item.id).sort())
      rules = body.ids.map((id, sort_order) => ({ ...rules.find(item => item.id === id)!, sort_order })); revision++
      return { json: rules, headers: { ETag: etag() } }
    } },
    { method: 'POST', path: '/api/admin/diagnostic-rules/preview', handler: async request => {
      const candidate = (await request.json()).rule as DiagnosticRule
      const matched = candidate.example === candidate.pattern && candidate.is_enabled
      await pending.preview?.()
      return { json: { matched, events: matched ? [{ ...candidate, id: 'candidate', rule_id: 0, raw_value: candidate.example, source_segments: ['errors'], view: candidate.preferred_view, sort_order: 0 }] : [] } }
    } },
    { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: [{ id: 'state', title: 'Состояние робота', is_enabled: true, roles: ['admin'], fields: [{ id: 9, path: 'data.status', label: 'Статус', sort_order: 0 }], sort_order: 0 }] }) },
  ] })
}

test('a delayed preview cannot restore a match after disabling the rule', async ({ page }) => {
  let release!: () => void; let released = false
  await installEditor(page, { preview: async () => { await new Promise<void>(resolve => { release = resolve }); released = true } })
  await page.setViewportSize({ width: 390, height: 900 })
  await page.goto('/admin/emergency/config?park=7&tab=indication&rule=1')
  await page.getByRole('button', { name: 'Проверить пример' }).click()
  await expect.poll(() => typeof release).toBe('function')
  await page.getByRole('button', { name: 'Отключить правило' }).click()
  await expect(page.getByLabel('Правило включено')).not.toBeChecked()
  release(); await expect.poll(() => released).toBe(true)
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await expect(page.getByText('Совпадение найдено', { exact: true })).toBeHidden()
  await expect(page.getByRole('button', { name: 'Проверить пример' })).toBeEnabled()
})

for (const selected of ['1', 'new']) test(`reselecting ${selected} preserves navigation and settles the pending preview`, async ({ page }) => {
  let release!: () => void; let previews = 0
  await installEditor(page, { preview: async () => { if (++previews === 1) await new Promise<void>(resolve => { release = resolve }) } })
  await page.setViewportSize({ width: 1440, height: 900 })
  const query = `?park=7&tab=indication&rule=${selected}&filter=unresolved&sort=title`
  await page.goto(`/admin/emergency/config${query}`)
  if (selected === 'new') for (const [label, value] of [['Название ошибки', 'Батарея'], ['Часть робота', 'Батарея'], ['Путь источника', 'errors'], ['Код или шаблон', 'BAT'], ['Расшифровка', 'Проверить батарею'], ['Пример входного значения', 'BAT']]) await page.getByLabel(label, { exact: true }).fill(value)
  const history = await page.evaluate(() => ({ key: window.history.state.key, length: window.history.length }))
  await page.getByRole('button', { name: 'Проверить пример' }).click()
  await expect.poll(() => typeof release).toBe('function')
  const sameSelection = page.getByRole('button', { name: selected === 'new' ? 'Новое правило' : `Открыть правило ${rule.title}`, exact: true })
  await sameSelection.click()
  await expect(sameSelection).toBeFocused()
  await sameSelection.press('Enter')
  await expect(sameSelection).toBeFocused()
  release()
  await expect(page.getByRole('button', { name: 'Проверить пример' })).toBeEnabled()
  await expect(page.getByText('Совпадение найдено', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => ({ key: window.history.state.key, length: window.history.length }))).toEqual(history)
  expect(new URL(page.url()).search).toBe(query)
  await page.getByRole('button', { name: 'Проверить пример' }).click()
  await expect.poll(() => previews).toBe(2)
  await expect(page.getByText('Совпадение найдено', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 900 })
  await expect(page.getByLabel('Название ошибки')).toHaveValue(selected === 'new' ? 'Батарея' : rule.title)
  await page.getByRole('button', { name: 'Назад к списку' }).click()
  await sameSelection.click()
  await expect(page.getByLabel('Название ошибки')).toBeVisible()
  expect(Object.fromEntries(new URL(page.url()).searchParams)).toEqual({ park: '7', tab: 'indication', rule: selected, filter: 'unresolved', sort: 'title' })
})

test('creation adopts its ID while retaining later edits and saves them with PATCH', async ({ page }) => {
  let release!: () => void; let posts = 0; const patches: string[] = []
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith('/admin/diagnostic-rules')) posts++; if (request.method() === 'PATCH') patches.push(request.url()) })
  await installEditor(page, { create: () => new Promise<void>(resolve => { release = resolve }) })
  await page.setViewportSize({ width: 390, height: 900 })
  await page.goto('/admin/emergency/config?park=7&tab=indication&rule=new')
  for (const [label, value] of [['Название ошибки', 'Батарея'], ['Часть робота', 'Батарея'], ['Путь источника', 'errors'], ['Код или шаблон', 'BAT'], ['Расшифровка', 'Проверить батарею'], ['Пример входного значения', 'BAT']]) await page.getByLabel(label, { exact: true }).fill(value)
  await page.getByRole('button', { name: 'Сохранить правило' }).click()
  await expect.poll(() => typeof release).toBe('function')
  await page.getByLabel('Название ошибки').fill('Батарея после отправки')
  await page.getByLabel('Ракурс', { exact: true }).selectOption('rear')
  release()
  await expect(page).toHaveURL(/rule=3/)
  await expect(page.getByLabel('Название ошибки')).toHaveValue('Батарея после отправки')
  await expect(page.getByLabel('Ракурс', { exact: true })).toHaveValue('rear')
  await page.getByRole('button', { name: 'Сохранить правило' }).click()
  await expect(page.getByText('Правило сохранено.', { exact: true })).toBeVisible()
  expect(posts).toBe(1); expect(patches).toHaveLength(1); expect(patches[0]).toMatch(/\/admin\/diagnostic-rules\/3$/)
  await page.reload(); await expect(page.getByLabel('Название ошибки')).toHaveValue('Батарея после отправки')
})

async function geometry(page: Page) {
  const overflow = await page.getByRole('main').evaluate(main => {
    const bounds = main.getBoundingClientRect()
    return Array.from(main.querySelectorAll('.rp-diagnostic-editor :is(input,select,textarea,button,figure,.rp-status-badge,.rp-diagnostic-marker),.rp-tabs')).filter(element => element.getClientRects().length).flatMap(element => {
      const rect = element.getBoundingClientRect()
      const panel = element.closest('.rp-master-detail__detail,.rp-master-detail__list')?.getBoundingClientRect() ?? bounds
      return rect.left < Math.max(0, bounds.left, panel.left) - 1 || rect.right > Math.min(innerWidth, bounds.right, panel.right) + 1 ? [{ tag: element.tagName, cls: element.className, left: rect.left, right: rect.right }] : []
    })
  })
  expect(overflow).toEqual([])
  for (const control of await page.locator('.rp-diagnostic-editor').locator('button:visible,input:not([type="checkbox"]):visible,select:visible,textarea:visible,.toggle:visible').all()) {
    const bounds = await control.boundingBox()
    const name = await control.evaluate(element => element.getAttribute('aria-label') || element.textContent || element.tagName)
    expect(Math.round(bounds!.width * 100) / 100, name).toBeGreaterThanOrEqual(44)
    expect(Math.round(bounds!.height * 100) / 100, name).toBeGreaterThanOrEqual(44)
  }
}

for (const theme of ['light', 'dark'] as const) test(`invalid coordinates remain contained at 390px ${theme}`, async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
  await installEditor(page)
  await page.goto('/admin/emergency/config?park=7&tab=indication&rule=1')
  for (const [axis, value] of [['X', '9'], ['X', '-1'], ['Y', '9'], ['Y', '-1']]) {
    const coordinate = page.getByLabel(`Координата ${axis}`)
    await coordinate.fill(value)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
    await expect(page.getByRole('img', { name: 'Маркер: Передний лидар' })).toBeHidden()
    await expect(coordinate).toHaveAttribute('aria-invalid', 'true')
    await expect(page.getByText('Укажите число от 0 до 1.', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Сохранить правило' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Проверить пример' })).toBeDisabled()
    await geometry(page)
    if (axis === 'X' && value === '9') {
      await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); document.querySelector('main')?.scrollTo(0, 0); window.scrollTo(0, 0) })
      await page.mouse.move(0, 0)
      await page.screenshot({ path: info.outputPath(`editor-invalid-${theme}-390.png`), fullPage: true, animations: 'disabled' })
    }
    await coordinate.fill('0.5')
    await expect(page.getByRole('img', { name: 'Маркер: Передний лидар' })).toBeVisible()
  }
})

for (const theme of ['light', 'dark'] as const) for (const width of [320, 390, 768, 1024, 1440]) {
  test(`editor places, previews and restores a rule at ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installEditor(page)
    await page.goto('/admin/emergency/config?park=7&tab=indication')
    await expect(page.getByRole('button', { name: `Открыть правило ${rule.title}` })).toBeVisible()
    await settlePage(page); await geometry(page)
    const tabs = page.getByRole('tablist', { name: 'Настройки проверки робота' })
    expect(await tabs.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1)
    await expect(page.getByRole('link', { name: 'Администрирование', exact: true }).first()).toHaveAttribute('aria-current', 'true')
    await page.screenshot({ path: info.outputPath(`editor-list-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
    await page.getByRole('button', { name: `Открыть правило ${rule.title}` }).click()
    if (width >= 600) {
      const source = (await page.getByLabel('Путь источника', { exact: true }).boundingBox())!
      const kind = (await page.getByLabel('Сопоставление', { exact: true }).boundingBox())!
      expect(source.y).toBeCloseTo(kind.y, 0)
    }
    const photo = page.getByRole('img', { name: 'Вид спереди', exact: true })
    await expect(photo).toBeVisible(); await photo.scrollIntoViewIfNeeded()
    await expect.poll(() => photo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true)
    const box = (await photo.boundingBox())!
    expect(box.width / box.height).toBeCloseTo(1547 / 2176, 2)
    await page.touchscreen.tap(box.x + box.width * .3, box.y + box.height * .7)
    expect(Number(await page.getByLabel('Координата X').inputValue())).toBeCloseTo(.3, 2)
    expect(Number(await page.getByLabel('Координата Y').inputValue())).toBeCloseTo(.7, 2)
    await page.getByLabel('Координата X').fill('0.4'); await page.getByLabel('Координата Y').fill('0.3')
    await page.getByLabel('Координата Y').press('ArrowUp')
    await expect(page.getByLabel('Координата Y')).toHaveValue('0.3001')
    await page.getByLabel('Координата Y').press('ArrowDown')
    await page.getByLabel('Координата Y').press('Tab')
    await page.getByRole('button', { name: 'Проверить пример' }).click()
    await expect(page.getByText('Совпадение найдено', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Сохранить правило' }).click()
    await expect(page.getByText('Правило сохранено.', { exact: true })).toBeVisible()
    await page.reload(); await expect(page.getByLabel('Координата X')).toHaveValue('0.4')
    await expect(page.getByLabel('Координата Y')).toHaveValue('0.3')
    const marker = page.getByRole('img', { name: 'Маркер: Передний лидар' })
    const markerBox = (await marker.boundingBox())!, restoredPhoto = (await photo.boundingBox())!
    expect((markerBox.x + markerBox.width / 2 - restoredPhoto.x) / restoredPhoto.width).toBeCloseTo(.4, 2)
    await page.getByLabel('Индикация', { exact: true }).selectOption('zone')
    await page.getByLabel('Ракурс', { exact: true }).selectOption('top')
    await expect(page.getByRole('img', { name: 'Вид сверху', exact: true })).toBeVisible()
    await expect(page.getByLabel('Координата X')).toHaveValue('0.4')
    await page.getByLabel('Координата X').fill('1')
    await geometry(page)
    await page.getByLabel('Координата X').fill('0.4')
    await geometry(page); await assertNoSeriousA11yViolations(page)
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); document.querySelector('main')?.scrollTo(0, 0); window.scrollTo(0, 0) })
    await page.mouse.move(0, 0)
    await page.screenshot({ path: info.outputPath(`editor-detail-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
    if (width < 900) {
      await page.getByRole('button', { name: 'Назад к списку' }).click()
      await expect(page.getByRole('button', { name: `Открыть правило ${rule.title}` })).toBeVisible()
      await expect(page.getByLabel('Название ошибки')).toBeHidden()
    }
    await page.getByRole('button', { name: `Ниже: ${rule.title}` }).click()
    await expect(page.getByText('Порядок сохранён.')).toBeVisible()
    await page.getByRole('tab', { name: 'Разделы и поля' }).click()
    await page.getByRole('button', { name: 'Открыть раздел Состояние робота' }).click()
    await expect(page.getByLabel('Путь поля 9')).toHaveValue('data.status')
    await expect(page).toHaveURL(/park=7/)
  })
}
