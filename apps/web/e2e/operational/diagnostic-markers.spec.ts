import { expect, test, type Page } from '@playwright/test'
import type { DiagnosticEvent } from '../../src/api'
import { installOperational, settlePage, snapshot } from './fixtures'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'

test.use({ trace: 'off', hasTouch: true })
const lidar: DiagnosticEvent = { id: 'lidar', rule_id: 1, source_path: 'errors.0', source_segments: ['errors', 0], raw_value: 'LIDAR_OFFLINE', title: 'Передний лидар недоступен', description: 'Проверьте питание и соединение переднего лидара.', severity: 'critical', sort_order: 0, part: 'Передний лидар', view: 'front', x: .25, y: .6, indicator: 'point' }
const events: DiagnosticEvent[] = [lidar,
  { ...lidar, id: 'battery', rule_id: 2, title: 'Перегрев батареи', description: 'Проверьте температуру батареи.', raw_value: 'BATTERY_HOT', part: 'Батарея', severity: 'warning', indicator: 'outline', x: .75, y: .3 },
  { ...lidar, id: 'edge', rule_id: 3, title: 'Проверить край корпуса', description: 'Осмотрите край корпуса.', raw_value: 'CASE_CHECK', part: 'Край корпуса', severity: 'info', indicator: 'zone', x: 1, y: 0 },
  { ...lidar, id: 'unknown', rule_id: null, title: 'Неизвестная ошибка', description: 'Для сигнала нет правила.', raw_value: { code: '<script>unsafe()</script>', message: 'Неизвестный сигнал с длинной строкой ' + 'Д'.repeat(120) }, part: null, view: null, x: null, y: null, indicator: null },
  { ...lidar, id: 'invalid', rule_id: 4, title: 'Сигнал с некорректным расположением', x: 9 },
]
async function geometry(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(page.viewportSize()!.width)
  const invalid = await page.locator('.rp-check-panel').evaluate(panel => {
    const bounds = panel.getBoundingClientRect()
    return Array.from(panel.querySelectorAll('.rp-check-event-marker,.rp-check-event-detail,.rp-check-events,.rp-check-event-actions button,.rp-check-event-detail button')).flatMap(element => {
      const rect = element.getBoundingClientRect()
      const frame = element.matches('.rp-check-event-marker') ? element.closest('.rp-check-photo-frame')!.getBoundingClientRect() : bounds
      return rect.left < Math.max(0, frame.left) - 1 || rect.right > Math.min(innerWidth, frame.right) + 1 || (element.matches('.rp-check-event-marker') && (rect.top < frame.top || rect.bottom > frame.bottom)) ? [{ cls: element.className, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom }] : []
    })
  })
  expect(invalid).toEqual([])
  for (const marker of await page.locator('.rp-check-event-marker').all()) {
    const rect = (await marker.boundingBox())!
    expect(rect.width).toBeGreaterThanOrEqual(44); expect(rect.height).toBeGreaterThanOrEqual(44)
    expect(await marker.evaluate(element => getComputedStyle(element).animationName)).toBe('none')
  }
}

for (const theme of ['light', 'dark'] as const) for (const width of [320, 390, 768, 1024, 1440]) {
  test(`server markers and explanations at ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 1000 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    let currentEvents = events
    await installOperational(page, { routes: [{ method: 'GET', path: /^\/api\/emergency\/[^/]+\/snapshot$/, handler: () => ({ json: { ...snapshot, diagnostic_events: currentEvents } }) }] })
    await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
    await expect(page.getByRole('button', { name: 'Спереди', exact: true })).toHaveAttribute('aria-pressed', 'true')
    const photo = page.locator('.rp-check-photo-frame > img')
    await photo.scrollIntoViewIfNeeded()
    await expect.poll(() => photo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true)
    const photoRect = (await photo.boundingBox())!
    expect(photoRect.width / photoRect.height).toBeCloseTo(1547 / 2176, 2)
    await expect(page.locator('.rp-check-event-marker')).toHaveCount(3)
    await geometry(page)
    const marker = page.getByRole('button', { name: 'Ошибка: Перегрев батареи', exact: true })
    await marker.focus(); await marker.press('Enter')
    const details = page.getByRole('region', { name: 'Выбранная ошибка' })
    await expect(details).toContainText('Проверьте температуру батареи.')
    await expect(marker).toHaveAttribute('aria-controls', await details.getAttribute('id') as string)
    await expect(marker).toBeFocused()
    const touchMarker = page.getByRole('button', { name: `Ошибка: ${lidar.title}`, exact: true })
    await touchMarker.tap()
    await expect(details).toContainText(lidar.description)
    const markerRect = (await touchMarker.boundingBox())!, activePhoto = (await photo.boundingBox())!
    expect((markerRect.x + markerRect.width / 2 - activePhoto.x) / activePhoto.width).toBeCloseTo(.25, 2)
    expect((markerRect.y + markerRect.height / 2 - activePhoto.y) / activePhoto.height).toBeCloseTo(.6, 2)
    await settlePage(page); await geometry(page); await assertNoSeriousA11yViolations(page)
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); document.querySelector('main')?.scrollTo(0, 0); window.scrollTo(0, 0) })
    await page.mouse.move(0, 0)
    await page.screenshot({ path: info.outputPath(`markers-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
    await page.getByRole('button', { name: 'Слева', exact: true }).click()
    currentEvents = [{ ...lidar, view: 'rear', sort_order: -1 }]
    await expect.poll(() => page.locator('.rp-check-event-detail').textContent()).toContain(lidar.description)
    await page.getByRole('button', { name: 'Все ошибки (5)', exact: true }).click()
    await expect(page.getByRole('tab', { name: 'Ошибки', exact: true })).toHaveAttribute('aria-selected', 'true')
    await page.clock.setFixedTime(new Date('2026-09-02T09:05:03Z'))
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
    await expect(page.getByRole('list', { name: 'Диагностические события' }).getByRole('listitem')).toHaveCount(1)
    await page.getByRole('tab', { name: 'Схема', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Слева', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await page.getByRole('button', { name: 'Показать ошибку', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Сзади', exact: true })).toHaveAttribute('aria-pressed', 'true')
    currentEvents = events
    await page.clock.setFixedTime(new Date('2026-09-02T09:05:06Z'))
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
    await page.getByRole('button', { name: /^Все ошибки/ }).click()
    const list = page.getByRole('list', { name: 'Диагностические события' })
    await expect(list).toContainText('Неизвестная ошибка')
    await expect(list).toContainText('Без локализации')
    await expect(list).toContainText('<script>unsafe()</script>')
    await expect(list.locator('script')).toHaveCount(0)
    await geometry(page); await assertNoSeriousA11yViolations(page)
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); document.querySelector('main')?.scrollTo(0, 0); window.scrollTo(0, 0) })
    await page.screenshot({ path: info.outputPath(`errors-${theme}-${width}.png`), fullPage: true, animations: 'disabled' })
  })
}

test('failed photo suppresses server markers but preserves the selected explanation and legacy wheels', async ({ page }) => {
  await installOperational(page, { snapshot: { ...snapshot, diagnostic_events: events } })
  await page.route(/\/assets\/robots\/front\.png(?:\?.*)?$/, route => route.request().resourceType() === 'image' ? route.abort() : route.continue())
  await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
  await expect(page.getByRole('img', { name: 'Схема модели робота', exact: true })).toBeVisible()
  await expect(page.locator('.rp-check-event-marker')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Выбранная ошибка' })).toContainText(lidar.description)
  await page.getByRole('button', { name: 'Переднее левое колесо: неисправность', exact: true }).click()
  await expect(page.getByText('Выбрано: Переднее левое колесо', { exact: true })).toBeVisible()
})

async function assertExplanationUncovered(page: Page) {
  const detail = page.getByRole('region', { name: 'Выбранная ошибка' })
  await expect(detail).toContainText('BATTERY_HOT')
  const failures = await detail.evaluate(region => {
    const nav = document.querySelector('.rp-shell__bottom-nav')!.getBoundingClientRect()
    const header = document.querySelector('.rp-shell__topbar')!.getBoundingClientRect()
    const bounds = region.getBoundingClientRect()
    const failures = bounds.top < header.bottom - 1 || bounds.bottom > nav.top + 1
      ? [`Detail ${bounds.top}..${bounds.bottom} outside clear area ${header.bottom}..${nav.top}`] : []
    for (const item of region.querySelectorAll('h3,.rp-status-badge,p,.rp-check-event-raw-label,pre,button,a')) {
      const rect = item.getBoundingClientRect()
      for (const y of [rect.top + 2, (rect.top + rect.bottom) / 2, rect.bottom - 2]) {
        const hit = document.elementFromPoint((rect.left + rect.right) / 2, y)
        if (!hit || !item.contains(hit)) failures.push(`${item.textContent}: covered at ${y} by ${hit?.className ?? 'outside viewport'}`)
      }
    }
    return failures
  })
  expect(failures).toEqual([])
}

for (const theme of ['light', 'dark'] as const) for (const width of [320, 390]) for (const height of [568, 700]) {
  test(`marker reveal clears fixed navigation at ${width}x${height} ${theme}`, async ({ page, context }, info) => {
    await page.setViewportSize({ width, height })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, { snapshot: { ...snapshot, diagnostic_events: events } })
    const session = await context.newCDPSession(page)
    await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
    const photo = page.locator('.rp-check-photo-frame > img')
    await expect(photo).toBeVisible()
    await expect.poll(() => photo.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true)
    const marker = page.getByRole('button', { name: 'Ошибка: Перегрев батареи', exact: true })
    // Undefined exercises the environment fallback; 34 exercises the actual CSS env value.
    let zeroInsetNavigationHeight = 0
    for (const bottom of [undefined, 0, 34]) {
      await session.send('Emulation.setSafeAreaInsetsOverride', { insets: bottom == null ? {} : { bottom } })
      await photo.evaluate(image => image.scrollIntoView({ block: 'start' }))
      await marker.tap()
      const navigation = (await page.locator('.rp-shell__bottom-nav').boundingBox())!
      if (bottom === 0) zeroInsetNavigationHeight = navigation.height
      if (bottom === 34) expect(navigation.height).toBeCloseTo(zeroInsetNavigationHeight + 34, 2)
      await assertExplanationUncovered(page)
      // Repeated selection and keyboard activation must reveal the same event again.
      await photo.evaluate(image => image.scrollIntoView({ block: 'start' }))
      await marker.focus()
      await marker.press('Enter')
      await assertExplanationUncovered(page)
      await expect(marker).toBeFocused()
      await page.screenshot({ path: info.outputPath(`reveal-${theme}-${width}x${height}-safe-${bottom ?? 'fallback'}.png`), animations: 'disabled' })
    }
    await session.detach()
  })
}

for (const width of [390, 1440]) test(`selection and passive snapshot/focus updates preserve the scroll position at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: width === 390 ? 700 : 1100 })
  let currentEvents = events; let reads = 0
  await installOperational(page, { routes: [{ method: 'GET', path: /^\/api\/emergency\/[^/]+\/snapshot$/, handler: () => { reads++; return { json: { ...snapshot, diagnostic_events: currentEvents } } } }] })
  await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
  const marker = page.getByRole('button', { name: 'Ошибка: Перегрев батареи', exact: true })
  await expect(marker).toBeVisible()
  expect(await page.evaluate(() => scrollY)).toBe(0)
  await marker.evaluate(element => (element as HTMLElement).focus({ preventScroll: true }))
  expect(await page.evaluate(() => scrollY)).toBe(0)
  await marker.press('Enter')
  if (width === 1440) expect(await page.evaluate(() => scrollY)).toBe(0)
  else await assertExplanationUncovered(page)
  await page.evaluate(() => window.scrollTo(0, 100))
  const before = await page.evaluate(() => scrollY)
  const previousReads = reads
  currentEvents = events.map(event => event.id === 'battery' ? { ...event, description: 'Проверьте температуру батареи повторно.' } : event)
  await page.clock.setFixedTime(new Date('2026-09-02T09:05:03Z'))
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  await expect.poll(() => reads).toBeGreaterThan(previousReads)
  await expect(page.getByRole('region', { name: 'Выбранная ошибка' })).toContainText('Проверьте температуру батареи повторно.')
  expect(await page.evaluate(() => scrollY)).toBe(before)
})
