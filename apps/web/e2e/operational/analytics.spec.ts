import { expect, test } from '@playwright/test'
import { analyticsFixture } from '../../src/domains/analytics/analytics.test-support'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, issue, settlePage } from './fixtures'

for (const { width, theme } of [{ width: 320, theme: 'light' }, { width: 320, theme: 'dark' }, { width: 390, theme: 'light' }, { width: 1440, theme: 'light' }] as const) test(`analytics shows measured period figures in the first screen at ${width} in ${theme}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 })
  await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
  await installOperational(page, { role: 'operator', routes: [{ method: 'GET', path: '/api/analytics', handler: request => {
    const params = new URL(request.url).searchParams
    return { json: analyticsFixture(Number(params.get('park_id')), Number(params.get('days')), params.get('bucket') === '2h' ? '2h' : '1d') }
  } }] })
  await page.goto('/analytics?park=7')
  const snapshot = page.getByRole('region', { name: 'Срез периода: Северный парк' })
  await expect(snapshot).toBeVisible()
  await expect(snapshot).toContainText('Открыто в среднем')
  await expect(snapshot).toContainText('2 задачи / снимок')
  await expect(snapshot).toContainText('Закрыто в Tracker')
  await expect(snapshot).toContainText('≥ 1')
  await expect(snapshot).toContainText('Неполное покрытие')
  const summaryBox = await snapshot.boundingBox()
  expect(summaryBox).toBeTruthy()
  await page.screenshot({ path: `/tmp/robopark-analytics-period-snapshot-${width}-${theme}.png` })
  if (width < 900) {
    const navigationBox = await page.locator('.rp-shell__bottom-nav').boundingBox()
    expect(navigationBox).toBeTruthy()
    expect(summaryBox!.y + summaryBox!.height).toBeLessThan(navigationBox!.y)
  } else {
    expect(summaryBox!.y + summaryBox!.height).toBeLessThan(900)
    const columns = await snapshot.locator('dl').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' '))
    expect(columns).toHaveLength(4)
  }
  const sla = page.locator('.rp-analytics-section').filter({ has: page.getByRole('heading', { name: 'Динамика SLA', exact: true }) })
  const age = page.locator('.rp-analytics-section').filter({ has: page.getByRole('heading', { name: 'Простой незавершённых задач', exact: true }) })
  await expect(age).not.toHaveAttribute('open')
  await sla.getByRole('heading', { name: 'Динамика SLA', exact: true }).scrollIntoViewIfNeeded()
  const warning = await sla.locator('.rp-analytics-warning').boundingBox()
  const chart = await sla.locator('.rp-analytics-card').boundingBox()
  expect(warning && chart).toBeTruthy()
  expect(chart!.y - warning!.y - warning!.height).toBeGreaterThanOrEqual(8)
  await page.screenshot({ path: `/tmp/robopark-analytics-sla-${width}-${theme}.png` })
  await age.getByRole('heading', { name: 'Простой незавершённых задач', exact: true }).click({ timeout: 1500 })
  await expect(age).toHaveAttribute('open')
})

test('all-park analytics keeps the available park visible after another park returns 503 on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await installOperational(page, { role: 'operator', routes: [{ method: 'GET', path: '/api/analytics', handler: request => {
    const params = new URL(request.url).searchParams
    const parkId = Number(params.get('park_id'))
    return parkId === 8
      ? { status: 503, json: { detail: 'analytics_upstream_unavailable' } }
      : { json: analyticsFixture(parkId, Number(params.get('days')), params.get('bucket') === '2h' ? '2h' : '1d') }
  } }] })
  await page.goto('/analytics?park=all')

  await expect(page.getByRole('region', { name: 'История парка Северный парк' })).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('Южный парк')
  await expect(page.getByRole('alert')).toContainText('Не удалось загрузить историю процесса')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await page.screenshot({ path: '/tmp/robopark-analytics-partial-phone.png' })
})

for (const theme of ['light', 'dark'] as const) for (const width of [320, 1440]) {
  test(`historical analytics Classic ${theme} ${width}: filters, coverage, comparison and drilldown`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    const requests: string[] = []
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/operations/overview') requests.push(request.url())
    })
    await installOperational(page, { role: 'operator', routes: [{ method: 'GET', path: '/api/analytics', handler: request => {
      const params = new URL(request.url).searchParams
      return { json: analyticsFixture(Number(params.get('park_id')), Number(params.get('days')), params.get('bucket') === '2h' ? '2h' : '1d') }
    } }] })
    await page.goto('/analytics?park=7')
    await expect(page.getByRole('heading', { name: 'Динамика процесса' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Текущие задачи' })).toHaveCount(0)
    await expect(page.getByText('Неполная история', { exact: true })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await page.getByLabel('Период аналитики').selectOption('1')
    await page.getByLabel('Шаг графиков').selectOption('2h')
    await expect(page).toHaveURL(/period=1&bucket=2h/)
    const arrivals = page.locator('article').filter({ has: page.getByRole('heading', { name: 'Поступило за период', exact: true }) })
    const intervals = arrivals.getByText('Значения по интервалам', { exact: true })
    await intervals.click()
    await expect(page.getByRole('table', { name: 'Поступило за период · Europe/Moscow' }).getByText('Нет наблюдений').first()).toBeVisible()
    await intervals.click()
    await page.getByLabel('Сравнить с парком').selectOption('8')
    if (width < 600) {
      await expect(page.getByRole('table', { name: 'Сравнение парков' })).toBeHidden()
      await expect(page.locator('.rp-analytics-comparison-cards')).toBeVisible()
    } else {
      await expect(page.getByRole('table', { name: 'Сравнение парков' })).toBeVisible()
    }
    await expect(page.getByRole('region', { name: 'История парка Южный парк' })).toBeVisible()
    await settlePage(page)
    await assertNoSeriousA11yViolations(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: testInfo.outputPath(`analytics-${theme}-${width}.png`) })
    const history = page.getByRole('region', { name: 'История парка Южный парк' })
    await history.getByText('Этапы работы и связанные задачи', { exact: true }).click()
    await history.getByText('Задачи в наблюдениях (1)', { exact: true }).click()
    const task = history.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' }).filter({ visible: true })
    await expect(task).toHaveAttribute('href', '/work/ROBOPARK-42?park=8')
    expect((await task.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    await task.click()
    await expect(page).toHaveURL(/\/work\/ROBOPARK-42\?park=8/)
    await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
    expect(requests).toEqual([])
  })
}

test('mobile comparison shows both parks as readable cards', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await installOperational(page, { role: 'operator', routes: [{ method: 'GET', path: '/api/analytics', handler: request => {
    const params = new URL(request.url).searchParams
    return { json: analyticsFixture(Number(params.get('park_id')), 7, '1d') }
  } }] })
  await page.goto('/analytics?park=7&compare=8')
  const cards = page.locator('.rp-analytics-comparison-cards')
  await expect(cards).toBeVisible()
  await expect(cards.locator('article')).toHaveCount(2)
  await expect(cards.getByRole('heading', { name: 'Северный парк' })).toBeVisible()
  await expect(cards.getByRole('heading', { name: 'Южный парк' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Показатели парков' })).toBeHidden()
  const comparisonHeading = await page.getByRole('heading', { name: 'Сравнение парков' }).boundingBox()
  const analysisHeading = await page.getByRole('heading', { name: 'Анализ текущей ситуации' }).boundingBox()
  expect(comparisonHeading && analysisHeading).toBeTruthy()
  expect(comparisonHeading!.y).toBeLessThan(analysisHeading!.y)
  const refresh = page.getByRole('button', { name: 'Обновить аналитику' })
  await expect(refresh).toBeVisible()
  expect((await refresh.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  expect((await page.getByLabel('Сравнить с парком').boundingBox())!.width).toBeGreaterThanOrEqual(250)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('analytics-comparison-mobile.png') })
})

test('a denied comparison recovers after removal through the real auth and park lifecycle', async ({ page }) => {
  const requestedParks: number[] = []
  let authReads = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/auth/me') authReads += 1
  })
  await installOperational(page, { role: 'operator', routes: [{ method: 'GET', path: '/api/analytics', handler: request => {
    const params = new URL(request.url).searchParams
    const parkId = Number(params.get('park_id'))
    requestedParks.push(parkId)
    return parkId === 8 ? { status: 403, json: { detail: 'park_forbidden' } }
      : { json: analyticsFixture(parkId, Number(params.get('days')), params.get('bucket') === '2h' ? '2h' : '1d') }
  } }] })
  await page.goto('/analytics?park=7')
  await expect(page.getByRole('region', { name: 'История парка Северный парк' })).toBeVisible()
  await settlePage(page)
  // StrictMode may bootstrap AuthProvider twice; measure only the denial's refresh.
  const initialAuthReads = authReads
  const initialParkReads = requestedParks.length
  await page.getByLabel('Сравнить с парком').selectOption('8')
  await expect(page.getByRole('heading', { name: 'Нет доступа' })).toBeVisible()
  await expect.poll(() => authReads).toBe(initialAuthReads + 1)
  await settlePage(page)
  expect(new Set(requestedParks.slice(initialParkReads))).toEqual(new Set([7, 8]))
  const deniedParkReads = requestedParks.length
  // Re-render the unchanged request context: a handled denial must not retry.
  // StrictMode can double the first requests when a keyed owner mounts.
  await page.getByLabel('Шаг графиков').selectOption('1d')
  await settlePage(page)
  expect(requestedParks).toHaveLength(deniedParkReads)
  await page.getByLabel('Сравнить с парком').selectOption('')
  await expect(page.getByRole('region', { name: 'История парка Северный парк' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Нет доступа' })).toHaveCount(0)
  expect(new Set(requestedParks.slice(deniedParkReads))).toEqual(new Set([7]))
  expect(authReads).toBe(initialAuthReads + 1)
})
