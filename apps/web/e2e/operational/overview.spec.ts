import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, issue, operationalRoutes, roles, settlePage, userForRole } from './fixtures'

test('overview task action follows the shared action color', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { user: userForRole('driver') })
  await page.goto('/overview?park=7')
  const task = page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })
  await expect(task).toBeVisible()
  const matchesActionToken = await task.evaluate((link) => {
    const probe = document.createElement('span')
    probe.style.color = 'var(--rp-action)'
    document.body.append(probe)
    const actionColor = getComputedStyle(probe).color
    probe.remove()
    return getComputedStyle(link).color === actionColor
  })
  expect(matchesActionToken).toBe(true)
  await page.screenshot({ path: '/tmp/robopark-overview-driver-phone-action.png' })
})

test('selected park overview still opens its campaign summary', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { role: 'royal', routes: [{
    method: 'GET', path: '/api/campaigns', handler: () => ({ json: [{
      id: 4, kind: 'service_company', name: 'Проверка парка', tracker_tag: 'service',
      starts_on: '2026-09-01', due_on: '2026-09-30', is_active: true,
      park_ids: [7], park_names: ['Северный парк'], total_count: 2, completed_count: 1,
      pending_review_count: 0, remaining_count: 1, percent_complete: 50, overdue: false,
    }] }),
  }] })
  await page.goto('/overview?park=7')
  await expect(page.getByRole('heading', { name: 'Очередь решений' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'СК и оклейка' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Проверка парка' })).toBeVisible()
})

for (const width of [320, 390, 1440]) test(`overview keeps the new task badge aligned at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await installOperational(page, { user: userForRole('operator') })
  await page.goto('/overview?park=7')
  const badge = page.locator('.rp-overview-task-table .rp-status-badge').filter({ hasText: 'Новый' }).first()
  await expect(badge).toBeVisible()
  const geometry = await badge.evaluate(element => {
    const style = getComputedStyle(element)
    const icon = element.querySelector('svg')?.getBoundingClientRect()
    const badge = element.getBoundingClientRect()
    return { display: style.display, width: badge.width, height: badge.height, iconCenterOffset: icon ? Math.abs(icon.top + icon.height / 2 - (badge.top + badge.height / 2)) : null }
  })
  expect(geometry.display).toBe('inline-flex')
  expect(geometry.width).toBeLessThan(100)
  expect(geometry.height).toBeLessThanOrEqual(36)
  expect(geometry.iconCenterOffset).not.toBeNull()
  expect(geometry.iconCenterOffset!).toBeLessThanOrEqual(2)
  await badge.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `/tmp/robopark-overview-badge-${width}.png` })
})

for (const width of [320, 390]) test(`overview keeps the park switch and task totals compact at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await installOperational(page, { user: userForRole('operator') })
  await page.goto('/overview')
  const summary = page.locator('.rp-overview-headline').first()
  await expect(summary.locator('.rp-metric-card')).toHaveCount(4)
  const park = await page.locator('.rp-shell__park-brand').boundingBox()
  const cards = await summary.locator('.rp-metric-card').all()
  const first = await cards[0].boundingBox()
  const second = await cards[1].boundingBox()
  expect(park).not.toBeNull()
  expect(first).not.toBeNull()
  expect(second).not.toBeNull()
  expect(park!.width).toBeLessThanOrEqual(190)
  expect(park!.height).toBeGreaterThanOrEqual(44)
  expect(park!.height).toBeLessThanOrEqual(52)
  const parkNameGeometry = await page.locator('.rp-shell__park-brand-name').evaluate(element => {
    const text = document.createRange()
    text.selectNodeContents(element)
    const style = getComputedStyle(element)
    return {
      textHeight: text.getBoundingClientRect().height,
      lineHeight: Number.parseFloat(style.lineHeight),
      textWidth: text.getBoundingClientRect().width,
      labelWidth: element.getBoundingClientRect().width,
      horizontalPadding: Number.parseFloat(style.paddingLeft) + Number.parseFloat(style.paddingRight),
    }
  })
  expect(parkNameGeometry.textHeight).toBeLessThanOrEqual(parkNameGeometry.lineHeight + 3)
  expect(parkNameGeometry.textWidth + parkNameGeometry.horizontalPadding).toBeLessThanOrEqual(parkNameGeometry.labelWidth)
  expect(first!.height).toBeLessThanOrEqual(120)
  expect(Math.abs(first!.y - second!.y)).toBeLessThanOrEqual(2)
  await expect(summary.locator('.rp-metric-card__delta')).toHaveCount(0)
  const valueCenterOffset = await summary.locator('.rp-metric-card__value').first().evaluate(element => {
    const card = element.closest('.rp-metric-card')!.getBoundingClientRect()
    const value = document.createRange()
    value.selectNodeContents(element)
    const text = value.getBoundingClientRect()
    return Math.abs(text.left + text.width / 2 - (card.left + card.width / 2))
  })
  expect(valueCenterOffset).toBeLessThanOrEqual(4)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: `/tmp/robopark-overview-compact-${width}.png` })
  if (width === 390) {
    await page.getByRole('button', { name: 'Сменить парк' }).click()
    await expect(page.getByRole('option', { name: 'Южный парк' })).toBeVisible()
    await page.getByRole('option', { name: 'Южный парк' }).click()
    await expect(page.locator('.rp-shell__park-brand-name')).toHaveText('Южный парк')
    await page.emulateMedia({ colorScheme: 'dark' })
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await page.screenshot({ path: '/tmp/robopark-overview-compact-dark-390.png' })
  }
})

test('overview totals remain readable with enlarged phone text', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { user: userForRole('operator') })
  await page.goto('/overview')
  await expect(page.locator('.rp-overview-headline').first().locator('.rp-metric-card')).toHaveCount(4)
  await page.addStyleTag({ content: 'html { font-size: 200% !important; }' })
  const summary = page.locator('.rp-overview-headline').first()
  const first = await summary.locator('.rp-metric-card').nth(0).boundingBox()
  const second = await summary.locator('.rp-metric-card').nth(1).boundingBox()
  expect(first).not.toBeNull()
  expect(second).not.toBeNull()
  expect(second!.y).toBeGreaterThanOrEqual(first!.y + first!.height)
  const clippedLabels = await summary.locator('.rp-metric-card__label').evaluateAll(labels => labels.filter(label => label.scrollWidth > label.clientWidth + 1).map(label => label.textContent))
  expect(clippedLabels).toEqual([])
  await summary.screenshot({ path: '/tmp/robopark-overview-compact-large-text-390.png' })
  const overflowing = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(element => {
    const bounds = element.getBoundingClientRect()
    return bounds.width > 0 && bounds.right > innerWidth + 1 && getComputedStyle(element).visibility !== 'hidden'
  }).slice(0, 12).map(element => `${element.tagName.toLowerCase()}.${element.className} right=${Math.round(element.getBoundingClientRect().right)}`))
  expect(overflowing).toEqual([])
})

test('desktop overview keeps four centered totals in one compact row', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await installOperational(page, { user: userForRole('operator') })
  await page.goto('/overview?park=7')
  const cards = page.locator('.rp-overview-headline .rp-metric-card')
  await expect(cards).toHaveCount(4)
  const park = await page.locator('.rp-shell__sidebar .rp-shell__park-brand').boundingBox()
  expect(park).not.toBeNull()
  expect(park!.width).toBeLessThanOrEqual(190)
  const bounds = await cards.evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()))
  expect(bounds.every(card => Math.abs(card.y - bounds[0].y) <= 2)).toBe(true)
  expect(bounds.every(card => card.height <= 120)).toBe(true)
  const centered = await cards.locator('.rp-metric-card__value').evaluateAll(values => values.every(element => {
    const card = element.closest('.rp-metric-card')!.getBoundingClientRect()
    const text = document.createRange()
    text.selectNodeContents(element)
    const value = text.getBoundingClientRect()
    return Math.abs(value.left + value.width / 2 - (card.left + card.width / 2)) <= 4
  }))
  expect(centered).toBe(true)
  await page.screenshot({ path: '/tmp/robopark-overview-compact-1440.png' })
})

for (const viewport of ['desktop', 'phone'] as const) for (const role of roles) {
  if (viewport === 'phone' && role !== 'operator' && role !== 'admin') continue
  test(`${role}${viewport === 'phone' ? ' phone' : ''}: selected park has an operations queue and SLA`, async ({ page }) => {
    if (viewport === 'phone') await page.setViewportSize({ width: 390, height: 900 })
    await installOperational(page, { user: userForRole(role) })
    await page.goto('/overview?park=7')

    await expect(page.getByRole('heading', { name: 'Что требует решения сейчас' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Статусы задач' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Очередь решений' })).toBeVisible()
    await expect(page.getByRole('region', { name: 'Сводка смены' }).getByText('SLA не определён')).toBeVisible()
    const taskRow = page.getByRole('row', { name: /ROBOPARK-42/ })
    await expect(taskRow.getByText('SLA: —', { exact: true })).toBeVisible()
    await expect(taskRow.getByText('Срок: Неизвестен', { exact: true })).toBeVisible()
    if (viewport === 'phone') {
      const overflowingMetrics = await page.locator('.rp-overview-headline .rp-metric-card__value').evaluateAll(values =>
        values.filter(value => value.scrollWidth > value.clientWidth + 1).map(value => value.textContent))
      expect(overflowingMetrics).toEqual([])
    }
    await expect(page.getByRole('heading', { name: 'Не найдено' })).toHaveCount(0)
    const task = page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })
    await expect(task).toBeVisible()
    await expect(task).toHaveAttribute('href', '/work/ROBOPARK-42?park=7')
    if (viewport === 'phone' && role !== 'driver' && role !== 'mechanic') {
      await expect(page.getByRole('heading', { name: 'Нагрузка по ответственным' })).toHaveCount(0)
      await page.getByRole('button', { name: 'Дополнительные показатели' }).click()
    }
    await expect(page.getByRole('heading', { name: 'Нагрузка по ответственным' })).toHaveCount(role === 'driver' || role === 'mechanic' ? 0 : 1)
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)

    if (viewport === 'phone') {
      await task.scrollIntoViewIfNeeded()
      await expect(task).toBeInViewport()
      expect((await task.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    }
    await task.click()
    await expect(page).toHaveURL(/\/work\/ROBOPARK-42\?park=7/)
    await expect(page.getByRole('heading', { name: 'Детали задачи', exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  })
}

test('royal selected park switches the task request instead of showing a fleet-only summary', async ({ page }) => {
  const requestedParks: string[] = []
  page.on('request', (request) => {
    const url = new URL(request.url())
    if (url.pathname === '/api/operations/overview') requestedParks.push(url.searchParams.get('park_id') ?? '')
  })
  await installOperational(page, { role: 'royal' })
  await page.goto('/overview?park=8')
  await expect(page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=8')
  expect(requestedParks).toContain('8')
})

test('driver opens the complete permitted work queue from overview', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await installOperational(page, { user: userForRole('driver') })
  await page.goto('/overview?park=7')

  const fullQueue = page.getByRole('link', { name: 'Вся очередь в «Работе»' })
  await expect(fullQueue).toHaveAttribute('href', '/work?park=7&status=all')
  await fullQueue.scrollIntoViewIfNeeded()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  expect((await fullQueue.boundingBox())!.x).toBeGreaterThanOrEqual(0)
  await fullQueue.click()

  await expect(page).toHaveURL(/\/work\?park=7&status=all$/)
  await expect(page.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('all')
})

test('overview recovers from park A denial when the user selects accessible park B', async ({ page }) => {
  const user = userForRole('royal')
  const overview = operationalRoutes({ user }).find(route => route.path === '/api/operations/overview')!
  await installOperational(page, { user, routes: [{ method: 'GET', path: '/api/operations/overview', handler: request =>
    new URL(request.url).searchParams.get('park_id') === '7'
      ? { status: 403, json: { detail: 'park_out_of_scope' } }
      : overview.handler(request),
  }] })
  await page.goto('/overview?park=7')
  await expect(page.getByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  await page.getByRole('button', { name: 'Сменить парк' }).click()
  await page.getByRole('option', { name: /Южный/ }).click()
  await expect(page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' })).toHaveAttribute('href', '/work/ROBOPARK-42?park=8')
  await expect(page.getByRole('heading', { name: 'Нет доступа' })).toHaveCount(0)
})
