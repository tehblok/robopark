import { expect, test, type Page } from '@playwright/test'
import { FIXED_TIME, installOperational, settlePage, snapshot, userForRole } from './fixtures'

// Exercise nonzero fleet jitter deterministically (first 15s, periodic 11s, resume 15s).
test.beforeEach(async ({ page }) => { await page.addInitScript(() => { Math.random = () => 0.5 }) })

async function selectSecondaryTab(page: Page, name: string) {
  const navigation = page.locator('.rp-check-navigation')
  await navigation.getByRole('button', { name: 'Ещё', exact: true }).click()
  await navigation.getByRole('menuitem', { name, exact: true }).click()
  await expect(page.getByRole('tab', { name, exact: true })).toHaveAttribute('aria-selected', 'true')
}

test('only admin can edit the global robot-check configuration', async ({ page }) => {
  const sections = [{ id: 'wheels', title: 'Колёса', is_enabled: true, roles: ['mechanic', 'admin'], fields: [{ id: 1, path: 'velocity', label: 'Скорость', sort_order: 0 }], sort_order: 0 }]
  const reading = {
    id: 1, section_id: 'wheels', path: 'parktronics.lt', label: 'Левый парктроник', display_kind: 'distance',
    unit: 'см', precision: 0, enabled_path: 'parktronics.ltEnabled', no_data_values: [2147483647],
    warning_below: 25, warning_above: null, critical_below: 10, critical_above: null,
    view: 'top', x: .24, y: .56, label_direction: 'left', is_enabled: true, sort_order: 0,
  }
  await installOperational(page, {
    role: 'royal',
    routes: [{ method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: sections }) }],
  })
  await page.goto('/admin/emergency/config?park=7&tab=readings')
  await expect(page.getByRole('tab', { name: 'Разделы и поля' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Показания' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Новый раздел' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Открыть раздел Колёса' }).click()
  await expect(page.getByLabel('Название wheels')).toHaveAttribute('readonly', '')
  await expect(page.getByRole('button', { name: 'Сохранить', exact: true })).toHaveCount(0)

  let saved = reading
  let written: { endpoint: string; body: Record<string, unknown> } | null = null
  await installOperational(page, {
    role: 'admin',
    routes: [
      { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: sections }) },
      { method: 'GET', path: '/api/admin/emergency-readings', handler: () => ({ json: [saved], headers: { ETag: '"readings-1"' } }) },
      { method: 'PATCH', path: '/api/admin/emergency-readings/1', handler: async request => {
        const body = await request.json() as Record<string, unknown>
        written = { endpoint: new URL(request.url).pathname, body }
        saved = { ...saved, ...body }
        return { json: saved }
      } },
    ],
  })
  await page.goto('/admin/emergency/config?park=7&tab=readings')
  await expect(page.getByRole('tab', { name: 'Показания' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('button', { name: 'Новое показание' })).toBeVisible()
  await page.getByRole('button', { name: 'Открыть показание Левый парктроник' }).click()
  await page.getByLabel('Название показания').fill('Левый парктроник кузова')
  await page.getByRole('button', { name: 'Сохранить показание' }).click()
  await expect(page.getByText('Показание сохранено.', { exact: true })).toBeVisible()
  expect(written).toMatchObject({
    endpoint: '/api/admin/emergency-readings/1',
    body: { label: 'Левый парктроник кузова', path: 'parktronics.lt', section_id: 'wheels' },
  })
  await expect(page.getByRole('button', { name: 'Открыть показание Левый парктроник кузова' })).toBeVisible()
})

test('mechanic sees role-filtered partial readings and explicit stale age', async ({ page }) => {
  await installOperational(page, { role: 'mechanic', snapshot: {
    ...snapshot,
    stale: true,
    stale_age_seconds: 37.4,
    battery1_percent: null,
    battery1_connected: true,
    battery2_percent: 0,
    battery2_connected: false,
    speed: 0,
    disk_percent: null,
    diagnostic_events: [],
    readings: [
      { id: 1, section_id: 'wheels', label: 'Ток колеса', display: '4,2 А', state: 'normal', view: 'top', x: .3, y: .6, label_direction: 'left' },
      { id: 2, section_id: 'wheels', label: 'Парктроник', display: 'Нет данных', state: 'unavailable', view: 'top', x: .65, y: .5, label_direction: 'right' },
    ],
  } })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  const summary = page.getByRole('region', { name: 'Состояние робота' })
  await expect(summary.locator('div').filter({ has: page.getByText('АКБ 1', { exact: true }) }).first()).toContainText('Нет данных')
  await expect(summary.locator('div').filter({ has: page.getByText('АКБ 2', { exact: true }) }).first()).toContainText('Не подключена')
  await expect(summary.locator('div').filter({ has: page.getByText('Скорость', { exact: true }) }).first()).toContainText('0 м/с')
  await expect(summary.locator('div').filter({ has: page.getByText('Диск', { exact: true }) }).first()).toContainText('Нет данных')
  await expect(summary).toContainText('Данные устарели · 37 с')
  await expect(summary).not.toContainText('Активных ошибок нет')
  const block = page.getByRole('region', { name: 'Диагностический блок «Колёса»' })
  await expect(block).toContainText('Ток колеса')
  await expect(block).toContainText('Парктроник')
  await expect(page.getByRole('link', { name: 'Открыть настройки' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /сохранить|отключить|удалить/i })).toHaveCount(0)
})

test('robot search stays first and makes no registry requests across reload and park changes', async ({ page }) => {
  const registryRequests: string[] = []
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/robots') registryRequests.push(request.url())
  })
  await installOperational(page, { role: 'operator' })
  await page.goto('/robots?park=7&q=447')
  await expect(page.locator('.rp-robots-page').getByRole('heading', { level: 2 })).toHaveText([
    'Открыть по номеру или сканировать', 'Недавние роботы',
  ])
  await expect(page.getByRole('heading', { name: 'Роботы в работе' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Сменить парк' }).click()
  await page.getByRole('option', { name: 'Южный парк' }).click()
  await expect(page).toHaveURL('/robots?park=8&q=447')
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await page.reload()
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await settlePage(page)
  expect(registryRequests).toEqual([])
  await page.getByRole('button', { name: 'Найти робота', exact: true }).click()
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=8`)
})

test('manual search survives reload and fetches Emergency only after opening a robot', async ({ page }) => {
  const emergency: string[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/emergency/')) emergency.push(url.pathname)
  })
  await installOperational(page)
  await page.goto('/robots?park=7')
  await page.getByLabel('Номер или VIN робота').fill('447')
  await expect(page).toHaveURL('/robots?park=7&q=447')
  await page.reload()
  await expect(page.getByLabel('Номер или VIN робота')).toHaveValue('447')
  await settlePage(page)
  expect(emergency).toEqual([])
  await page.getByRole('button', { name: 'Найти робота', exact: true }).click()
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7`)
  await selectSecondaryTab(page, 'Задачи')
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7&tab=tasks`)
  await expect(page.getByRole('tab', { name: 'Задачи', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
  expect(emergency.length).toBeGreaterThan(0)
  await expect(page.locator('.rp-shell__desktop-nav').getByRole('link', { name: 'Роботы', exact: true })).toHaveAttribute('aria-current', 'page')
  await selectSecondaryTab(page, 'История')
  await expect(page.getByRole('tabpanel')).toContainText('История событий пока недоступна')
})

for (const state of ['pending', 'failed'] as const) test(`direct robot tasks stay usable with a ${state} Emergency snapshot`, async ({ page }) => {
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  await installOperational(page, { routes: [{ method: 'GET', path: /^\/api\/emergency\/[^/]+\/snapshot$/, handler: async () => {
    if (state === 'pending') await pending
    return { status: 502, json: { detail: 'emergency_upstream_error' } }
  } }] })
  try {
    await page.goto(`/robots/${snapshot.vin}?park=7&tab=tasks`)
    await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
    await selectSecondaryTab(page, 'Телеметрия')
    await expect(page.getByRole('tabpanel')).toHaveAccessibleName('Телеметрия')
    await selectSecondaryTab(page, 'Задачи')
    await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
  } finally { release() }
})

test('empty recent robots leave manual search usable on a narrow phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await installOperational(page)
  await page.goto('/robots?park=7')
  await expect(page.getByText('Недавно открытых роботов нет.')).toBeVisible()
  await expect(page.getByLabel('Номер или VIN робота')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0)
  await page.getByLabel('Номер или VIN робота').fill('447')
  await page.getByRole('button', { name: 'Найти робота', exact: true }).click()
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7`)
})

test('a restored workspace tab is horizontally visible on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await installOperational(page)
  await page.goto(`/robots/${snapshot.vin}?park=7&tab=scheme`)
  const tab = page.getByRole('tab', { name: 'Схема', exact: true })
  await expect(tab).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.rp-check-navigation').getByRole('tab')).toHaveText(['Состояние', 'Ошибки', 'Схема'])
  await expect(page.locator('.rp-check-navigation').getByRole('button', { name: 'Ещё', exact: true })).toBeVisible()
  const summary = await page.getByRole('region', { name: 'Состояние робота' }).boundingBox()
  const detail = await page.locator('.rp-check-detail').boundingBox()
  expect(summary!.y + summary!.height).toBeLessThanOrEqual(detail!.y + 1)
  const bounds = await page.getByRole('tablist').boundingBox()
  const selected = await tab.boundingBox()
  // Fractional tab widths meet integer clientWidth and browser scroll rounding.
  expect(selected!.x).toBeGreaterThanOrEqual(bounds!.x - 1)
  expect(selected!.x + selected!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1)
})

for (const reference of ['447', 'YASADR00000000447', 'https://robopark.example.invalid/emergency?q=447&tab=wheels&park=7']) {
  test(`resolves manual reference ${reference}`, async ({ page }) => {
    const resolved: string[] = []
    const trackerRequests: string[] = []
    page.on('request', request => {
      const path = new URL(request.url()).pathname
      if (path === '/api/emergency/resolve') resolved.push(request.postDataJSON().robot_number)
      if (path.startsWith('/api/tracker/')) trackerRequests.push(path)
    })
    await installOperational(page, { role: 'driver' })
    await page.goto('/robots?park=7')
    await page.getByLabel('Номер или VIN робота').fill(reference)
    await page.getByRole('button', { name: 'Найти робота', exact: true }).click()
    await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7`)
    await expect(page.getByRole('heading', { name: 'Робот 447', exact: true })).toBeVisible()
    await expect(page.getByText(snapshot.vin, { exact: true })).toBeHidden()
    await page.getByText('VIN и координаты', { exact: true }).click()
    await expect(page.getByText(snapshot.vin, { exact: true })).toBeVisible()
    expect(resolved[0]).toBe(reference.startsWith('https:') ? '447' : reference)
    await selectSecondaryTab(page, 'Задачи')
    await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
    await settlePage(page)
    expect(trackerRequests.length).toBeGreaterThan(0)
    expect(new Set(trackerRequests)).toEqual(new Set([`/api/tracker/robots/${snapshot.vin}/tickets`]))
  })
}

test('short route canonicalizes to the compact summary without loading a competing identity photo', async ({ page }) => {
  const photos: string[] = []
  page.on('request', request => { if (request.resourceType() === 'image' && /\/assets\/robots\/.+\.webp/.test(request.url())) photos.push(new URL(request.url()).pathname.split('/').at(-1)!) })
  await installOperational(page)
  await page.goto('/robots/447?park=7')
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7`)
  const summary = page.getByRole('region', { name: 'Состояние робота' })
  await expect(summary.locator('div').filter({ has: page.getByText('АКБ 1', { exact: true }) }).first()).toContainText('85 %')
  await expect(summary.locator('div').filter({ has: page.getByText('АКБ 2', { exact: true }) }).first()).toContainText('83 %')
  await expect(page.getByText(snapshot.vin, { exact: true })).toBeHidden()
  await settlePage(page)
  expect(photos).toEqual(['top.webp'])
  await expect(page.locator('.rp-robot-identity')).toHaveCount(0)
})

test('recents expire at 48 hours and persist the pruned list after reload', async ({ page }) => {
  const user = userForRole('driver')
  const fresh = { query: '448', vin: 'YASADR00000000448', openedAt: Date.parse('2026-08-31T09:05:00.001Z') }
  await installOperational(page, { user })
  await page.goto('/robots?park=7')
  await expect(page.getByLabel('Номер или VIN робота')).toBeVisible()
  await page.evaluate(({ id, fresh }) => localStorage.setItem(`robopark.recentRobots.v2.${id}`, JSON.stringify([
    { query: '447', vin: 'YASADR00000000447', openedAt: Date.parse('2026-08-31T09:05:00Z') },
    fresh,
    { query: '449', vin: 'YASADR00000000449', openedAt: Date.parse('2026-08-30T09:05:00Z') },
  ])), { id: user.id, fresh })
  await page.reload()
  await expect(page.locator('.rp-robots-recent-list').getByRole('link')).toHaveCount(1)
  await expect(page.locator('.rp-robots-recent-list').getByRole('link')).toContainText('448')
  expect(await page.evaluate(id => JSON.parse(localStorage.getItem(`robopark.recentRobots.v2.${id}`)!), user.id)).toEqual([fresh])
  await page.clock.setFixedTime(new Date('2026-09-02T09:05:00.001Z'))
  await page.reload()
  await expect(page.getByText('Недавно открытых роботов нет.')).toBeVisible()
  expect(await page.evaluate(id => JSON.parse(localStorage.getItem(`robopark.recentRobots.v2.${id}`)!), user.id)).toEqual([])
})

test('v2 recents isolate two users, survive reload and clear only current namespace', async ({ page }) => {
  const user = userForRole('driver')
  const other = { ...user, id: 999, username: 'other-driver' }
  let current = user
  await installOperational(page, { routes: [{ method: 'GET', path: '/api/auth/me', handler: () => ({ json: current }) }] })
  await page.goto('/robots?park=7')
  await page.getByLabel('Номер или VIN робота').fill('447')
  await page.getByRole('button', { name: 'Найти робота', exact: true }).click()
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7`)
  await page.goto('/robots?park=7')
  await expect(page.locator('.rp-robots-recent-list').getByRole('link')).toContainText('447')
  await page.reload()
  await expect(page.locator('.rp-robots-recent-list').getByRole('link')).toContainText('447')
  current = other
  await page.reload()
  await expect(page.getByText('Недавно открытых роботов нет.')).toBeVisible()
  // Simultaneous namespaces model an independently remembered robot for this account.
  await page.evaluate(({ id, time }) => localStorage.setItem(`robopark.recentRobots.v2.${id}`, JSON.stringify([{ query: '448', vin: 'YASADR00000000448', openedAt: Date.parse(time) }])), { id: other.id, time: FIXED_TIME })
  await page.reload()
  await expect(page.locator('.rp-robots-recent-list').getByRole('link')).toContainText('448')
  await expect(page.locator('.rp-robots-recent-list')).not.toContainText('447')
  await page.getByRole('button', { name: 'Очистить', exact: true }).click()
  await expect(page.getByText('Недавно открытых роботов нет.')).toBeVisible()
  current = user
  await page.reload()
  await expect(page.locator('.rp-robots-recent-list').getByRole('link')).toContainText('447')
})

for (const route of [`/robots/${snapshot.vin}/check?tab=wheels&park=7`, '/emergency?q=447&tab=wheels&park=7']) {
  test(`restores canonical check and keyboard tabs from ${route}`, async ({ page }) => {
    let snapshots = 0
    page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/snapshot')) snapshots += 1 })
    await installOperational(page)
    await page.clock.install({ time: new Date('2026-09-02T09:05:00Z') })
    await page.goto(route)
    await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7&tab=wheels`)
    const wheels = page.getByRole('tab', { name: 'Колёса', exact: true })
    await expect(wheels).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tabpanel')).toContainText('Неисправность: требуется проверка')
    await page.reload()
    await expect(wheels).toHaveAttribute('aria-selected', 'true')
    await wheels.focus()
    await page.keyboard.press('ArrowLeft')
    await expect(page.getByRole('tab', { name: 'Схема', exact: true })).toBeFocused()
    await expect(page.getByRole('tab', { name: 'Схема', exact: true })).toHaveAttribute('aria-selected', 'true')
    await page.keyboard.press('ArrowRight')
    await expect(page.getByRole('tab', { name: 'Состояние', exact: true })).toBeFocused()
    await expect(page.getByRole('tab', { name: 'Состояние', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(wheels).toHaveCount(0)
    await selectSecondaryTab(page, 'Карта')
    await page.getByRole('tab', { name: 'Карта', exact: true }).focus()
    await expect(page.getByRole('tab', { name: 'Карта', exact: true })).toBeFocused()
    await expect(page.getByRole('button', { name: 'Слежение включено', exact: true })).toBeVisible()
    await expect(page.locator('.leaflet-container')).toBeVisible()
    const before = snapshots
    await expect(page.getByRole('button', { name: /^Обновить/ })).toHaveCount(0)
    await page.clock.runFor(15_000)
    await expect.poll(() => snapshots).toBeGreaterThan(before)
  })
}

test('driver canonical check loads sections, automatically refreshes, and requests scoped Tracker work', async ({ page }) => {
  const trackerRequests: string[] = []
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/tracker/')) trackerRequests.push(new URL(request.url()).pathname)
  })
  await installOperational(page, { role: 'driver' })
  await page.clock.install({ time: new Date('2026-09-02T09:05:00Z') })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=wheels`)
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7&tab=wheels`)
  await expect(page.getByRole('heading', { name: 'Робот 447', exact: true })).toBeVisible()
  await expect(page.getByText('Робот на связи', { exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Колёса', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel')).toContainText('Неисправность: требуется проверка')
  await settlePage(page)
  await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `/api/emergency/${snapshot.vin}/snapshot` && response.status() === 200),
    page.clock.runFor(15_000),
  ])
  await page.getByRole('tab', { name: 'Схема', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Схема', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.rp-check-wheel-details')).toContainText('Неисправность: Переднее левое колесо')
  await selectSecondaryTab(page, 'Задачи')
  await expect(page.getByRole('link', { name: 'Открыть ROBOPARK-42' })).toBeVisible()
  await settlePage(page)
  expect(trackerRequests.length).toBeGreaterThan(0)
  expect(new Set(trackerRequests)).toEqual(new Set([`/api/tracker/robots/${snapshot.vin}/tickets`]))
})

test('robot offline and browser offline remain different states with automatic recovery', async ({ page, context }) => {
  let snapshots = 0
  page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/snapshot')) snapshots += 1 })
  await installOperational(page, { snapshot: { ...snapshot, online: false } })
  await page.clock.install({ time: new Date('2026-09-02T09:05:00Z') })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=wheels`)
  await expect(page.getByText('Робот не в сети', { exact: true })).toBeVisible()
  await expect(page.getByText('Нет сети на этом устройстве', { exact: true })).toHaveCount(0)
  await context.setOffline(true)
  await expect(page.getByText('Нет сети на этом устройстве', { exact: true })).toBeVisible()
  await expect(page.getByText('Робот не в сети', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /^Обновить/ })).toHaveCount(0)
  const before = snapshots
  await page.clock.runFor(20_000)
  expect(snapshots).toBe(before)
  await context.setOffline(false)
  await expect(page.getByText('Нет сети на этом устройстве', { exact: true })).toHaveCount(0)
  expect(snapshots).toBe(before)
  await page.clock.runFor(15_000)
  await expect.poll(() => snapshots).toBeGreaterThan(before)
  await expect(page.getByText('Робот не в сети', { exact: true })).toBeVisible()
})

test('map stays inside its panel and does not intercept the scheme tab', async ({ page }) => {
  await installOperational(page)
  await page.goto(`/robots/${snapshot.vin}/check?park=7`)
  const map = page.locator('.leaflet-container')
  await expect(map).toBeVisible()
  const mapBox = await map.boundingBox()
  const panelBox = await page.getByRole('tabpanel').boundingBox()
  expect(mapBox!.x).toBeGreaterThanOrEqual(panelBox!.x)
  expect(mapBox!.y).toBeGreaterThanOrEqual(panelBox!.y)
  expect(mapBox!.x + mapBox!.width).toBeLessThanOrEqual(panelBox!.x + panelBox!.width)
  expect(mapBox!.y + mapBox!.height).toBeLessThanOrEqual(panelBox!.y + panelBox!.height)
  const attributionLinks = page.locator('.leaflet-control-attribution a')
  await expect(attributionLinks.first()).toBeVisible()
  for (const link of await attributionLinks.all()) {
    const box = await link.boundingBox()
    expect(box!.width).toBeGreaterThanOrEqual(44)
    expect(box!.height).toBeGreaterThanOrEqual(44)
    expect(await link.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(14)
  }
  await page.getByRole('tab', { name: 'Схема', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Схема', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.rp-check-photo-frame img')).toBeVisible()
})

test('scanner cancellation stops the fake camera track', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await page.addInitScript(() => {
    const state = { requested: false, stopped: false }
    Object.defineProperty(window, '__cameraTest', { value: state })
    const stream = new MediaStream()
    Object.defineProperty(stream, 'getTracks', { value: () => [{ stop: () => { state.stopped = true } }] })
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => { state.requested = true; return stream } } })
    Object.defineProperty(window, 'BarcodeDetector', { value: class { async detect() { return [] } } })
    HTMLMediaElement.prototype.play = async () => {}
  })
  await installOperational(page)
  await page.goto('/robots?park=7')
  await page.getByRole('button', { name: 'Сканировать', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Сканировать робота' })
  await expect(dialog).toBeVisible()
  expect(await page.evaluate(() => Reflect.get(window, '__cameraTest').requested)).toBe(false)
  await dialog.getByRole('button', { name: 'Включить камеру', exact: true }).click()
  await expect.poll(() => page.evaluate(() => Reflect.get(window, '__cameraTest').requested)).toBe(true)
  await dialog.getByRole('button', { name: 'Отменить', exact: true }).click()
  await expect(dialog).toBeHidden()
  expect(await page.evaluate(() => Reflect.get(window, '__cameraTest').stopped)).toBe(true)
})

for (const view of [
  { id: 'top', label: 'сверху', width: 1200, height: 1242 },
  { id: 'front', label: 'спереди', width: 1200, height: 1688 },
  { id: 'rear', label: 'сзади', width: 1200, height: 1819 },
  { id: 'left', label: 'слева', width: 1200, height: 1687 },
  { id: 'right', label: 'справа', width: 1200, height: 1704 },
  { id: 'isometric', label: 'изометрия', width: 1200, height: 1361 },
] as const) {
  test(`active diagnostic event automatically selects ${view.id} WebP view`, async ({ page }) => {
    const images: string[] = []
    page.on('request', request => { if (request.resourceType() === 'image' && /\/assets\/robots\/.+\.webp/.test(request.url())) images.push(new URL(request.url()).pathname.split('/').at(-1)!) })
    const event = {
      id: `event-${view.id}`, rule_id: 1, source_path: 'errors.0', source_segments: ['errors', 0],
      raw_value: 'FAULT', title: 'Активная ошибка', description: 'Проверить робот.', severity: 'critical' as const,
      sort_order: 0, part: 'Робот', view: view.id, x: .5, y: .5, indicator: 'point' as const,
    }
    await installOperational(page, { snapshot: { ...snapshot, diagnostic_events: [event] } })
    await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
    const photo = page.locator('.rp-check-photo-frame img')
    await photo.scrollIntoViewIfNeeded()
    await expect(photo).toHaveAttribute('alt', new RegExp(view.label))
    await expect.poll(() => photo.evaluate((element: HTMLImageElement) => [element.naturalWidth, element.naturalHeight])).toEqual([view.width, view.height])
    await expect(page.getByRole('button', { name: 'Ошибка: Активная ошибка' })).toBeVisible()
    expect(images).toEqual([`${view.id}.webp`])
    await expect(page.getByRole('group', { name: 'Ракурс модели' })).toHaveCount(0)
    await expect(page.locator('.rp-check-wheel')).toHaveCount(0)
  })
}

test('photo failure uses neutral fallback and unknown faults never invent body or sensor markers', async ({ page }) => {
  await installOperational(page, { snapshot: { ...snapshot, wheels_fault: ['body', 'unknown-sensor'] } })
  await page.route(/\/assets\/robots\/top\.webp(?:\?.*)?$/, route => route.request().resourceType() === 'image' ? route.abort() : route.continue())
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await page.locator('.rp-check-photo-frame').scrollIntoViewIfNeeded()
  await expect(page.getByRole('img', { name: 'Схема модели робота', exact: true })).toBeVisible()
  await expect(page.getByText('Неисправность колёс: точное расположение не определено', { exact: true })).toBeVisible()
  await expect(page.locator('.rp-check-wheel--fault')).toHaveCount(0)
  await expect(page.locator('.rp-check-photo-frame').getByRole('button', { name: /корпус|датчик|body|sensor/i })).toHaveCount(0)
})
