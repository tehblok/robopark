import { expect, test } from '@playwright/test'
import { userForRole } from './fixtures'
import { openRouteFixture, routeSchedule } from './routeFixtures'

test.use({ hasTouch: true, locale: 'ru-RU' })

test('operator schedule fixture shows the empty notification state instead of a mock 404', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'schedule', userForRole('operator'))

  await expect(page.getByText('Новых уведомлений нет')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Не удалось загрузить уведомления' })).toHaveCount(0)
})

test('notification controls keep a panel gap before the empty state', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'schedule', userForRole('driver'))

  const controls = await page.locator('.rp-notification-system').boundingBox()
  const empty = await page.locator('.rp-notification-system + .rp-empty-state').boundingBox()
  expect(controls && empty).toBeTruthy()
  expect(empty!.y - controls!.y - controls!.height).toBeGreaterThanOrEqual(8)
})

test('driver sees the correct role in a personal schedule template on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await openRouteFixture(page, 'schedule', userForRole('driver'))
  await page.getByRole('tab', { name: 'Шаблоны' }).click()
  const participants = page.locator('.rp-schedule__employees')
  await expect(participants.getByRole('checkbox', { name: 'driver-e2e · Водитель' })).toBeChecked()
  await page.getByRole('combobox', { name: 'Тип графика', exact: true }).selectOption('3/3')
  await page.getByLabel('Первый день смены', { exact: true }).fill('2026-10-01')
  await page.getByLabel('Создавать до', { exact: true }).fill('2026-10-31')
  await page.getByLabel('Время начала', { exact: true }).fill('09:00')
  await page.getByLabel('Время окончания', { exact: true }).fill('21:00')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await participants.screenshot({ path: '/tmp/robopark-schedule-driver-template-phone.png' })
  await page.getByRole('region', { name: 'Мои шаблоны' }).screenshot({ path: '/tmp/robopark-schedule-driver-planner-phone.png' })
  const create = page.getByRole('button', { name: 'Создать смены' })
  await expect(create).toBeEnabled()
  await create.scrollIntoViewIfNeeded()
  await page.screenshot({ path: '/tmp/robopark-schedule-driver-viewport-phone.png' })
  const createBox = await create.boundingBox()
  const navigationBox = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(createBox && navigationBox).toBeTruthy()
  expect(createBox!.y + createBox!.height).toBeLessThanOrEqual(navigationBox!.y)
})

test('desktop schedule keeps the team grid sticky across range navigation', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 1000 })
  await openRouteFixture(page, 'schedule', userForRole('royal'), { routes: [{
    method: 'GET',
    path: '/api/schedules/participants',
    handler: () => ({ json: [
      { id: 100, display_name: 'Анна Механик', role: 'mechanic' },
      ...Array.from({ length: 11 }, (_, index) => ({
        id: 110 + index,
        display_name: `Сотрудник ${index + 1}`,
        role: 'mechanic' as const,
      })),
    ] }),
  }] })

  const grid = page.getByTestId('schedule-team-grid')
  await expect(grid).toBeVisible()
  await expect(grid.getByRole('rowheader', { name: 'Анна Механик · Механик' })).toBeVisible()
  const monthView = page.getByRole('button', { name: 'Месяц' })
  await monthView.click()
  await expect(monthView).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => grid.getByRole('columnheader').count()).toBeGreaterThan(8)

  const scroller = page.locator('.rp-schedule-team__matrix-scroll')
  const header = grid.locator('.rp-schedule-team__header-group')
  const corner = grid.locator('.rp-schedule-team__corner')
  const employee = grid.locator('.rp-schedule-team__employee').first()
  const before = {
    header: (await header.boundingBox())!,
    corner: (await corner.boundingBox())!,
    employee: (await employee.boundingBox())!,
  }
  const scrollRange = await scroller.evaluate(element => ({
    x: element.scrollWidth - element.clientWidth,
    y: element.scrollHeight - element.clientHeight,
  }))
  expect(scrollRange.x).toBeGreaterThan(0)
  expect(scrollRange.y).toBeGreaterThan(0)
  await scroller.evaluate(element => element.scrollTo({ left: 320, top: 240 }))
  await expect.poll(() => scroller.evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }))).toMatchObject({ left: 320, top: 240 })
  const after = {
    header: (await header.boundingBox())!,
    corner: (await corner.boundingBox())!,
    employee: (await employee.boundingBox())!,
  }
  expect(Math.abs(after.header.y - before.header.y)).toBeLessThanOrEqual(1)
  expect(Math.abs(after.corner.x - before.corner.x)).toBeLessThanOrEqual(1)
  expect(Math.abs(after.corner.y - before.corner.y)).toBeLessThanOrEqual(1)
  expect(Math.abs(after.employee.x - before.employee.x)).toBeLessThanOrEqual(1)

  const firstDay = grid.getByRole('columnheader').nth(1)
  const initialDay = await firstDay.getAttribute('aria-label')
  await page.getByRole('button', { name: 'Следующий период' }).click()
  await expect(firstDay).not.toHaveAttribute('aria-label', initialDay!)
})

test('royal can edit a team schedule entry', async ({ page }) => {
  let update: unknown
  await page.setViewportSize({ width: 1366, height: 1000 })
  await openRouteFixture(page, 'schedule', userForRole('royal'), { routes: [{
    method: 'POST',
    path: '/api/sync/batch',
    handler: async request => {
      const batch = await request.json() as { actions: Array<{ client_action_id: string; payload: Record<string, unknown> }> }
      update = batch.actions[0]?.payload
      return { json: {
        results: batch.actions.map(item => ({
          client_action_id: item.client_action_id, state: 'confirmed', code: null,
          result: { entry: { ...routeSchedule, ...item.payload, updated_at: '2026-09-02T09:10:00Z' } },
        })),
        deltas: {}, revisions: {}, revoked_scopes: [],
      } }
    },
  }] })

  await page.getByRole('button', { name: 'Изменить' }).click()
  await expect(page.getByRole('heading', { name: 'Изменить период' })).toBeVisible()
  const startAt = await page.getByLabel('Начало').inputValue()
  await page.getByLabel('Конец').fill('2026-09-02T22:00')
  const endAt = await page.getByLabel('Конец').inputValue()
  await page.getByRole('button', { name: 'Сохранить' }).click()

  await expect.poll(() => update).toMatchObject({
    kind: 'shift',
    start_at: new Date(startAt).toISOString(),
    end_at: new Date(endAt).toISOString(),
  })
  const time = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
  await expect(page.getByTestId('schedule-team-grid').getByText(`${time.format(new Date(startAt))} — ${time.format(new Date(endAt))}`)).toBeVisible()
  await page.screenshot({ path: '/tmp/robopark-schedule-team-desktop.png' })
})

test('phone shows a durable pending period after a failed batch and reload', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await openRouteFixture(page, 'schedule', userForRole('mechanic'), { routes: [{
    method: 'POST', path: '/api/sync/batch', handler: () => ({ status: 503, json: { detail: 'offline' } }),
  }] })
  await page.getByRole('button', { name: 'Добавить период' }).click()
  await page.getByLabel('Начало').fill('2026-09-02T09:00')
  await page.getByLabel('Конец').fill('2026-09-02T21:00')
  await page.getByRole('button', { name: 'Сохранить' }).click()
  await expect(page.getByText('Ожидает синхронизации')).toBeVisible()
  await page.reload()
  await expect(page.getByText('Ожидает синхронизации')).toBeVisible()
  const pending = page.locator('.rp-schedule-entry').filter({ hasText: 'Ожидает синхронизации' })
  await pending.scrollIntoViewIfNeeded()
  const card = await pending.boundingBox()
  const nav = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(card && nav).toBeTruthy()
  expect(card!.x).toBeGreaterThanOrEqual(0)
  expect(card!.x + card!.width).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-schedule-offline-pending-phone.png' })
  await page.getByRole('button', { name: /Открыть центр синхронизации/ }).click()
  const darkCenter = page.getByRole('region', { name: 'Центр синхронизации' })
  await expect(darkCenter.getByText('Создать период · График')).toBeVisible()
  await darkCenter.screenshot({ path: '/tmp/robopark-sync-queue-phone-dark.png' })
  await darkCenter.getByRole('button', { name: 'Закрыть центр синхронизации' }).click()
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  const secondaryColors = await page.getByRole('button', { name: 'Предыдущий период' }).evaluate(element => ({
    background: getComputedStyle(element).backgroundColor,
    text: getComputedStyle(element).color,
  }))
  expect(secondaryColors).toEqual({ background: 'rgb(255, 255, 255)', text: 'rgb(32, 35, 34)' })
  await expect(page.getByText('Ожидает синхронизации')).toBeVisible()
  await page.screenshot({ path: '/tmp/robopark-schedule-offline-pending-phone-light.png' })
  await page.getByRole('button', { name: /Открыть центр синхронизации/ }).click()
  const center = page.getByRole('region', { name: 'Центр синхронизации' })
  await expect(center.getByText('Создать период · График')).toBeVisible()
  await center.screenshot({ path: '/tmp/robopark-sync-queue-phone-light.png' })
  await center.getByRole('button', { name: 'Отменить «Создать период · График»' }).click()
  await expect(center.getByText('Создать период · График')).toHaveCount(0)
  await expect(page.getByText('Ожидает синхронизации')).toHaveCount(0)
})

test('phone explains a rejected offline action inside the sync panel', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await openRouteFixture(page, 'schedule', userForRole('mechanic'), { routes: [{
    method: 'POST', path: '/api/sync/batch', handler: async request => {
      const body = await request.json() as { actions: Array<{ client_action_id: string }> }
      return { json: {
        results: body.actions.map(item => ({ client_action_id: item.client_action_id,
          state: 'rejected', code: 'park_forbidden', result: null })),
        deltas: {}, revisions: {}, revoked_scopes: [],
      } }
    },
  }] })
  await page.getByRole('button', { name: 'Добавить период' }).click()
  await page.getByLabel('Начало').fill('2026-09-02T09:00')
  await page.getByLabel('Конец').fill('2026-09-02T21:00')
  await page.getByRole('button', { name: 'Сохранить' }).click()
  await page.getByRole('button', { name: /Открыть центр синхронизации/ }).click()
  const center = page.getByRole('region', { name: 'Центр синхронизации' })
  await expect(center.getByText('Не завершено: 1')).toBeVisible()
  await expect(center.getByText('Доступ к парку изменился. Отмените действие и проверьте доступ перед повтором.')).toBeVisible()
  await expect(center.getByText('park_forbidden')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  const panelBox = await center.boundingBox()
  const cancelBox = await center.getByRole('button', { name: 'Отменить «Создать период · График»' }).boundingBox()
  expect(panelBox && cancelBox).toBeTruthy()
  expect(cancelBox!.x + cancelBox!.width).toBeLessThanOrEqual(panelBox!.x + panelBox!.width - 8)
  expect(await center.getByRole('button', { name: 'Отменить «Создать период · График»' })
    .evaluate(button => button.scrollWidth <= button.clientWidth)).toBe(true)
  await center.screenshot({ path: '/tmp/robopark-sync-rejection-phone-dark.png' })
})

test('fallback IndexedDB lease keeps an offline period after reload without Web Locks', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => Object.defineProperty(navigator, 'locks', { value: undefined, configurable: true }))
  await openRouteFixture(page, 'schedule', userForRole('mechanic'), { routes: [{
    method: 'POST', path: '/api/sync/batch', handler: () => ({ status: 503, json: { detail: 'offline' } }),
  }] })
  expect(await page.evaluate(() => navigator.locks)).toBeUndefined()
  await page.getByRole('button', { name: 'Добавить период' }).click()
  await page.getByLabel('Начало').fill('2026-09-02T09:00')
  await page.getByLabel('Конец').fill('2026-09-02T21:00')
  await page.getByRole('button', { name: 'Сохранить' }).click()
  await expect(page.getByText('Ожидает синхронизации')).toBeVisible()
  await page.reload()
  await expect(page.getByText('Ожидает синхронизации')).toBeVisible()
  await page.getByRole('button', { name: /Открыть центр синхронизации/ }).click()
  await expect(page.getByRole('region', { name: 'Центр синхронизации' }).getByText('Создать период · График')).toBeVisible()
})

test('320px phone keeps the real sync queue and cancel action inside the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 })
  await openRouteFixture(page, 'schedule', userForRole('mechanic'), { routes: [{
    method: 'POST', path: '/api/sync/batch', handler: () => ({ status: 503, json: { detail: 'offline' } }),
  }] })
  await page.getByRole('button', { name: 'Добавить период' }).click()
  await page.getByLabel('Начало').fill('2026-09-02T09:00')
  await page.getByLabel('Конец').fill('2026-09-02T21:00')
  await page.getByRole('button', { name: 'Сохранить' }).click()
  await expect(page.getByText('Ожидает синхронизации')).toBeVisible()
  await page.getByRole('button', { name: /Открыть центр синхронизации/ }).click()
  const center = page.getByRole('region', { name: 'Центр синхронизации' })
  await expect(center.getByText('Создать период · График')).toBeVisible()
  const cancel = center.getByRole('button', { name: 'Отменить «Создать период · График»' })
  await expect(cancel).toBeVisible()
  const bounds = await center.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await center.screenshot({ path: '/tmp/robopark-sync-queue-320.png' })
})

test('driver keeps a queued template after a phone reload', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await openRouteFixture(page, 'schedule', userForRole('driver'), { routes: [{
    method: 'POST', path: '/api/sync/batch', handler: () => ({ status: 503, json: { detail: 'offline' } }),
  }] })
  await page.getByRole('tab', { name: 'Шаблоны' }).click()
  await page.getByLabel('Первый день смены').fill('2026-09-02')
  await page.getByLabel('Создавать до').fill('2026-09-02')
  await page.getByLabel('Время начала').fill('09:00')
  await page.getByLabel('Время окончания').fill('21:00')
  await page.getByRole('button', { name: 'Создать смены' }).click()
  await page.getByRole('button', { name: /Открыть центр синхронизации/ }).click()
  let center = page.getByRole('region', { name: 'Центр синхронизации' })
  await expect(center.getByText('Применить шаблон · График')).toBeVisible()
  await center.getByRole('button', { name: 'Закрыть центр синхронизации' }).click()
  await page.reload()
  await page.getByRole('tab', { name: 'Шаблоны' }).click()
  await page.getByRole('button', { name: /Открыть центр синхронизации/ }).click()
  center = page.getByRole('region', { name: 'Центр синхронизации' })
  await expect(center.getByText('Применить шаблон · График')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-schedule-template-pending-phone.png' })
})

for (const width of [360, 390] as const) {
  test(`phone-${width} uses day cards without a compressed grid or document overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 360 ? 800 : 844 })
    await openRouteFixture(page, 'schedule', userForRole('mechanic'))
    await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
    await page.getByRole('button', { name: '2 сентября' }).tap()
    await expect(page.getByText('Моя смена')).toBeVisible()
    await expect(page.getByTestId('schedule-team-grid')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)

    await openRouteFixture(page, 'schedule', userForRole('royal'))
    await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
    await expect(page.getByTestId('schedule-team-grid')).toBeHidden()
    const unscheduled = page.getByTestId('schedule-day-cards').getByRole('heading', { name: 'Олег Оператор' })
    await expect(unscheduled).toBeVisible()
    await unscheduled.scrollIntoViewIfNeeded()
    const person = await unscheduled.boundingBox()
    const navigation = await page.locator('.rp-shell__bottom-nav').boundingBox()
    expect(person && navigation).toBeTruthy()
    expect(person!.y + person!.height).toBeLessThan(navigation!.y)
    const tabs = await page.getByRole('tablist', { name: 'Разделы графика' }).boundingBox()
    const planning = await page.getByRole('tab', { name: 'Планирование' }).boundingBox()
    expect(tabs && planning).toBeTruthy()
    expect(planning!.x + planning!.width).toBeLessThanOrEqual(tabs!.x + tabs!.width)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
  })
}
test('tablet schedule keeps range actions together and shows all seven days', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 900 })
  await openRouteFixture(page, 'schedule', userForRole('driver'))
  const controls = page.locator('.rp-schedule__range button')
  await expect(controls).toHaveCount(3)
  const boxes = await Promise.all([0, 1, 2].map(index => controls.nth(index).boundingBox()))
  expect(boxes.every(Boolean)).toBe(true)
  expect(Math.max(...boxes.map(box => box!.y)) - Math.min(...boxes.map(box => box!.y))).toBeLessThanOrEqual(2)
  const days = await page.locator('.rp-schedule-calendar__days--week').evaluate(element => ({ visible: element.clientWidth, full: element.scrollWidth }))
  expect(days.full, 'week row should fit its calendar card at tablet width').toBeLessThanOrEqual(days.visible + 1)
  await page.getByRole('button', { name: 'Месяц', exact: true }).click()
  const month = await page.locator('.rp-schedule-calendar__days--month').evaluate(element => ({ visible: element.clientWidth, full: element.scrollWidth }))
  expect(month.full, 'month columns should fit their calendar card at tablet width').toBeLessThanOrEqual(month.visible + 1)
})

test('phone schedule keeps swipeable days and an explicit scroll hint', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'schedule', userForRole('driver'))
  await expect(page.getByText('Листайте дни →')).toBeVisible()
  const days = await page.locator('.rp-schedule-calendar__days--week').evaluate(element => ({ visible: element.clientWidth, full: element.scrollWidth }))
  expect(days.full).toBeGreaterThan(days.visible)
  await page.locator('.rp-schedule-calendar__day').last().scrollIntoViewIfNeeded()
  await expect(page.locator('.rp-schedule-calendar__day').last()).toBeInViewport()
})
