import { expect, test } from '@playwright/test'
import { FIXED_TIME, installOperational, settlePage, snapshot, userForRole } from './fixtures'

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
    await expect(page.getByText(snapshot.vin, { exact: true })).toBeVisible()
    expect(resolved[0]).toBe(reference.startsWith('https:') ? '447' : reference)
    await settlePage(page)
    expect(trackerRequests.length).toBeGreaterThan(0)
    expect(new Set(trackerRequests)).toEqual(new Set([`/api/tracker/robots/${snapshot.vin}/tickets`]))
  })
}

test('short route canonicalizes and identity loads only the selected original photo', async ({ page }) => {
  const photos: string[] = []
  page.on('request', request => { if (request.resourceType() === 'image' && /\/assets\/robots\/.+\.png/.test(request.url())) photos.push(new URL(request.url()).pathname.split('/').at(-1)!) })
  await installOperational(page)
  await page.goto('/robots/447?park=7')
  await expect(page).toHaveURL(`/robots/${snapshot.vin}?park=7`)
  const photo = page.getByRole('img', { name: 'Иллюстрация модели робота', exact: true })
  await expect(photo).toBeVisible()
  await expect(photo).toHaveAttribute('loading', 'lazy')
  await expect.poll(() => photo.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(1962)
  await settlePage(page)
  expect(photos).toEqual(['isometric.png'])
  await expect(page.locator('.rp-robot-identity img')).toHaveCount(1)
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
    await page.goto(route)
    await expect(page).toHaveURL(`/robots/${snapshot.vin}/check?park=7&tab=wheels`)
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
    await expect(wheels).toBeFocused()
    await page.keyboard.press('ArrowRight')
    await expect(page.getByRole('tab', { name: 'Карта', exact: true })).toBeFocused()
    await expect(page.getByRole('button', { name: 'Слежение включено', exact: true })).toBeVisible()
    await expect(page.locator('.leaflet-container')).toBeVisible()
    const before = snapshots
    await page.getByRole('button', { name: 'Обновить данные робота', exact: true }).click()
    await expect.poll(() => snapshots).toBeGreaterThan(before)
  })
}

test('driver canonical check loads sections, refreshes, and requests scoped Tracker work', async ({ page }) => {
  const trackerRequests: string[] = []
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/tracker/')) trackerRequests.push(new URL(request.url()).pathname)
  })
  await installOperational(page, { role: 'driver' })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=wheels`)
  await expect(page).toHaveURL(`/robots/${snapshot.vin}/check?park=7&tab=wheels`)
  await expect(page.getByRole('heading', { name: 'Робот 447', exact: true })).toBeVisible()
  await expect(page.getByText('Робот на связи', { exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Колёса', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tabpanel')).toContainText('Неисправность: требуется проверка')
  await settlePage(page)
  await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `/api/emergency/${snapshot.vin}/snapshot` && response.status() === 200),
    page.getByRole('button', { name: 'Обновить данные робота', exact: true }).click(),
  ])
  await page.getByRole('tab', { name: 'Схема', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Схема', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('button', { name: 'Переднее левое колесо: неисправность', exact: true })).toBeVisible()
  await settlePage(page)
  expect(trackerRequests.length).toBeGreaterThan(0)
  expect(new Set(trackerRequests)).toEqual(new Set([`/api/tracker/robots/${snapshot.vin}/tickets`]))
})

test('robot offline and browser offline remain different actionable states', async ({ page, context }) => {
  await installOperational(page, { snapshot: { ...snapshot, online: false } })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=wheels`)
  await expect(page.getByText('Робот не в сети', { exact: true })).toBeVisible()
  await expect(page.getByText('Нет сети на этом устройстве', { exact: true })).toHaveCount(0)
  await context.setOffline(true)
  await expect(page.getByText('Нет сети на этом устройстве', { exact: true })).toBeVisible()
  await expect(page.getByText('Робот не в сети', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Обновить данные робота', exact: true })).toBeEnabled()
  await context.setOffline(false)
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
  await expect.poll(() => page.evaluate(() => Reflect.get(window, '__cameraTest').requested)).toBe(true)
  await dialog.getByRole('button', { name: 'Отменить', exact: true }).click()
  await expect(dialog).toBeHidden()
  expect(await page.evaluate(() => Reflect.get(window, '__cameraTest').stopped)).toBe(true)
})

test('all six original views load only on selection with correct visible wheel mapping', async ({ page }) => {
  const images: string[] = []
  page.on('request', request => { if (request.resourceType() === 'image' && /\/assets\/robots\/.+\.png/.test(request.url())) images.push(new URL(request.url()).pathname.split('/').at(-1)!) })
  await installOperational(page)
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  const views = [
    { label: 'Сверху', file: 'top.png', width: 2269, height: 2347, wheels: ['Переднее левое', 'Среднее левое', 'Заднее левое', 'Переднее правое', 'Среднее правое', 'Заднее правое'] },
    { label: 'Спереди', file: 'front.png', width: 1547, height: 2176, wheels: ['Переднее правое', 'Переднее левое'] },
    { label: 'Сзади', file: 'rear.png', width: 1454, height: 2204, wheels: ['Заднее левое', 'Заднее правое'] },
    { label: 'Слева', file: 'left.png', width: 1610, height: 2263, wheels: ['Переднее левое', 'Среднее левое', 'Заднее левое'] },
    { label: 'Справа', file: 'right.png', width: 1638, height: 2325, wheels: ['Заднее правое', 'Среднее правое', 'Переднее правое'] },
    { label: 'Изометрия', file: 'isometric.png', width: 1962, height: 2225, wheels: [] },
  ]
  for (const [index, view] of views.entries()) {
    await page.getByRole('group', { name: 'Ракурс модели' }).getByRole('button', { name: view.label, exact: true }).click()
    const photo = page.locator('.rp-check-photo-frame img')
    await photo.scrollIntoViewIfNeeded()
    await expect(photo).toHaveCount(1)
    await expect.poll(() => photo.evaluate((element: HTMLImageElement) => [element.naturalWidth, element.naturalHeight])).toEqual([view.width, view.height])
    expect(images).toEqual(Array.from(new Set(['isometric.png', ...views.slice(0, index + 1).map(item => item.file)])))
    const markers = page.locator('.rp-check-wheel')
    await expect(markers).toHaveCount(view.wheels.length)
    for (const [wheelIndex, label] of view.wheels.entries()) {
      await expect(markers.nth(wheelIndex)).toHaveAttribute('aria-label', `${label} колесо: ${label === 'Переднее левое' ? 'неисправность' : 'ошибка не сообщена'}`)
    }
    if (view.wheels.length > 1 && view.label !== 'Сверху') {
      const first = await markers.first().boundingBox()
      const last = await markers.last().boundingBox()
      expect(first!.x).toBeLessThan(last!.x)
    }
  }
  await page.getByRole('button', { name: 'Показать колёса сверху', exact: true }).click()
  const failed = page.getByRole('button', { name: 'Переднее левое колесо: неисправность', exact: true })
  await failed.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Выбрано: Переднее левое колесо', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Открыть данные колёс', exact: true }).click()
  await expect(page.getByRole('tab', { name: 'Колёса', exact: true })).toHaveAttribute('aria-selected', 'true')
})

test('photo failure uses neutral fallback and unknown faults never invent body or sensor markers', async ({ page }) => {
  await installOperational(page, { snapshot: { ...snapshot, wheels_fault: ['body', 'unknown-sensor'] } })
  await page.route(/\/assets\/robots\/top\.png(?:\?.*)?$/, route => route.request().resourceType() === 'image' ? route.abort() : route.continue())
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await page.locator('.rp-check-photo-frame').scrollIntoViewIfNeeded()
  await expect(page.getByRole('img', { name: 'Схема модели робота', exact: true })).toBeVisible()
  await expect(page.getByText('Неисправность колёс: точное расположение не определено', { exact: true })).toBeVisible()
  await expect(page.locator('.rp-check-wheel--fault')).toHaveCount(0)
  await expect(page.locator('.rp-check-photo-frame').getByRole('button', { name: /корпус|датчик|body|sensor/i })).toHaveCount(0)
})
