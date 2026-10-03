import { expect, test } from '@playwright/test'
import type { Paged, TrackerIssueDetail } from '../../src/api'
import { FIXED_TIME, installOperational, issue, parkNorth, settlePage, snapshot, userForRole } from './fixtures'

test('transferred task keeps the first park SLA clock on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const currentPark = { ...parkNorth, timezone: 'Asia/Yekaterinburg' }
  const transferred = {
    ...issue,
    queued_at: '2026-09-18T12:00:00Z',
    sla_deadline: '2026-09-18T17:00:00Z',
    sla_source: 'status_history' as const,
    sla_timezone: 'Europe/Moscow',
    workflow: { owner: null, review_state: null, display_status: 'queued' as const, sync_state: 'saved' as const, has_current_cycle_comment: false },
  }
  await installOperational(page, {
    user: { ...userForRole('operator'), parks: [currentPark] },
    parks: [currentPark], issue: transferred,
  })
  await page.clock.setFixedTime(new Date('2026-09-18T14:00:00Z'))
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  await expect(page.getByText('SLA: 3:00', { exact: true }).last()).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-transferred-sla-phone.png' })
})

test('open mechanic work refreshes the queue without repeatedly fetching the defect catalog', async ({ page }) => {
  let queueRequests = 0
  let defectRequests = 0
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (path === '/api/tracker/issues') queueRequests += 1
    if (path === '/api/tracker/defect-codes') defectRequests += 1
  })
  await installOperational(page, { role: 'mechanic' })
  await page.clock.install({ time: new Date('2026-09-02T09:05:00Z') })
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ })).toBeVisible()
  await expect.poll(() => defectRequests).toBe(1)
  await settlePage(page)
  const initialQueueRequests = queueRequests
  await page.clock.runFor(61_000)
  await expect.poll(() => queueRequests).toBeGreaterThan(initialQueueRequests)
  expect(defectRequests).toBe(1)
  await page.getByRole('link', { name: 'Обзор', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Что требует решения сейчас' })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  await expect.poll(() => defectRequests).toBe(2)
  await settlePage(page)
  await page.clock.fastForward(600_001)
  await expect.poll(() => defectRequests).toBeGreaterThan(2)
})

test('returning to Work checks the queue immediately while retaining the local shell', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  let version = 1
  let queueRequests = 0
  await installOperational(page, { role: 'mechanic', routes: [{
    method: 'GET', path: '/api/tracker/issues', handler: request => {
      if (new URL(request.url).searchParams.get('owned_by_me') === 'true') return { json: { items: [], total: 0, limit: 50, offset: 0, has_more: false } }
      queueRequests += 1
      const current = { ...issue, status: 'В очереди', status_key: 'queued', assignee: null, summary: version === 1 ? 'Старая формулировка' : 'Обновлённая формулировка' }
      return { json: { items: [current], total: 1, limit: 50, offset: 0, has_more: false } }
    },
  }] })
  await page.goto('/work?park=7')
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42: Старая формулировка/ })).toBeVisible()
  await page.getByRole('link', { name: 'Обзор', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Что требует решения сейчас' })).toBeVisible()
  version = 2
  await page.getByRole('link', { name: 'Работа', exact: true }).click()
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42: Обновлённая формулировка/ })).toBeVisible()
  expect(queueRequests).toBeGreaterThanOrEqual(2)
})

test('mechanic sees owned work and an honest empty queue on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const owned = { ...issue, status: 'В работе', status_key: 'in_progress', assignee: { login: 'mechanic-e2e', display: 'Механик смены' } }
  await installOperational(page, { role: 'mechanic', routes: [{
    method: 'GET', path: '/api/tracker/issues', handler: request => {
      const mine = new URL(request.url).searchParams.get('owned_by_me') === 'true'
      return { json: { items: mine ? [owned] : [], total: mine ? 1 : 0, limit: 50, offset: 0, has_more: false } }
    },
  }] })
  await page.goto('/work?park=7')
  await expect(page.getByRole('heading', { name: 'Мои открытые задачи' })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ })).toBeVisible()
  await expect(page.getByText('Задач по фильтру: 0')).toBeVisible()
  await expect(page.getByText('В очереди парка сейчас нет задач.')).toBeVisible()
  await expect(page.getByRole('navigation', { name: 'Страницы задач' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})

test('mechanic keeps an unconfirmed offline claim after reloading the phone view', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  const unassigned = { ...issue, status: 'В очереди', status_key: 'queued', assignee: null }
  let directClaimRequests = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname === `/api/tracker/issues/${issue.key}/claim`) directClaimRequests += 1
  })
  await installOperational(page, { role: 'mechanic', routes: [
    { method: 'GET', path: '/api/tracker/issues', handler: request => {
      const mine = new URL(request.url).searchParams.get('owned_by_me') === 'true'
      return { json: { items: mine ? [] : [unassigned], total: mine ? 0 : 1, limit: 50, offset: 0, has_more: false } }
    } },
    { method: 'POST', path: '/api/sync/batch', handler: () => ({ status: 503, json: { detail: 'offline' } }) },
  ] })
  await page.goto('/work?park=7')
  await page.getByRole('button', { name: 'Взять в работу' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'Взятие ожидает подтверждения' })).toBeVisible()
  expect(directClaimRequests).toBe(0)

  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: 'Взятие ожидает подтверждения' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Взять в работу' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320)
  await settlePage(page)
  await page.screenshot({ path: '/tmp/robopark-offline-claim-phone.png', fullPage: true })
  // Stop the intentionally retrying sync engine before Playwright removes this
  // test's API route during fixture teardown.
  await page.close({ runBeforeUnload: false })
})

test('confirmed batched claim moves one task into the mechanic view', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  let claimed = false
  let batchedClaims = 0
  let directClaimRequests = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname === `/api/tracker/issues/${issue.key}/claim`) directClaimRequests += 1
  })
  await installOperational(page, { role: 'mechanic', routes: [
    { method: 'GET', path: '/api/tracker/issues', handler: request => {
      const mine = new URL(request.url).searchParams.get('owned_by_me') === 'true'
      const current = { ...issue, status: 'В очереди', status_key: 'queued', assignee: claimed
        ? { login: 'mechanic-e2e', display: 'Механик смены' } : null }
      return { json: {
        items: mine ? claimed ? [current] : [] : [current],
        total: mine ? Number(claimed) : 1, limit: 50, offset: 0, has_more: false,
      } }
    } },
    { method: 'POST', path: '/api/sync/batch', handler: async request => {
      const batch = await request.json() as { actions: Array<{ client_action_id: string; action: string; park_id: number }> }
      batchedClaims += batch.actions.filter(action => action.action === 'claim' && action.park_id === parkNorth.id).length
      claimed = true
      return { json: {
        results: batch.actions.map(action => ({ client_action_id: action.client_action_id, state: 'confirmed', code: null, result: {} })),
        deltas: {}, revisions: {}, revoked_scopes: [],
      } }
    } },
  ] })
  await page.goto('/work?park=7')
  await page.getByRole('button', { name: 'Взять в работу' }).click()

  await expect.poll(() => batchedClaims).toBe(1)
  await expect(page.getByRole('button', { name: 'Мои задачи (1)' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Взять в работу' })).toHaveCount(0)
  expect(directClaimRequests).toBe(0)
})

test('direct My Tasks view loads only owned work and stays usable on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const queries: URLSearchParams[] = []
  const owned = { ...issue, status: 'В работе', status_key: 'in_progress', assignee: { login: 'mechanic-e2e', display: 'Механик смены' } }
  await installOperational(page, { role: 'mechanic', routes: [{
    method: 'GET', path: '/api/tracker/issues', handler: request => {
      const query = new URL(request.url).searchParams
      queries.push(query)
      return { json: { items: [owned], total: 1, limit: 50, offset: 0, has_more: false } }
    },
  }] })
  await page.goto('/work?park=7&view=mine')
  await expect(page.getByRole('heading', { name: 'Мои задачи' })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ })).toBeVisible()
  expect(queries.length).toBeGreaterThan(0)
  expect(queries.every(query => query.get('owned_by_me') === 'true')).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})

test('mechanic sees oldest queued work before owned repairs on phone and desktop', async ({ page }) => {
  const owner = { login: 'mechanic-e2e', display: 'Механик смены' }
  const older = { ...issue, key: 'ROBOPARK-OLDER', summary: 'Первый в очереди', status_key: 'queued', status: 'В очереди', assignee: null,
    queued_at: '2026-09-01T09:00:00Z', sla_deadline: '2026-09-01T14:00:00Z', sla_source: 'status_history' as const }
  const newer = { ...issue, key: 'ROBOPARK-NEWER', summary: 'Следующий в очереди', status_key: 'queued', status: 'В очереди', assignee: null,
    queued_at: '2026-09-02T09:00:00Z', sla_deadline: '2026-09-02T14:00:00Z', sla_source: 'status_history' as const }
  const unknown = { ...issue, key: 'ROBOPARK-UNKNOWN', summary: 'Время очереди уточняется', status_key: 'queued', status: 'В очереди', assignee: null,
    created_at: '2025-01-01T00:00:00Z', queued_at: null, sla_deadline: null, sla_source: null }
  const owned = { ...issue, key: 'ROBOPARK-OWNED', summary: 'Уже в ремонте', status_key: 'in_progress', status: 'В работе', assignee: owner,
    queued_at: '2026-09-01T10:00:00Z', sla_deadline: '2026-09-01T15:00:00Z', sla_source: 'status_history' as const }
  const ownedQueries: number[] = []
  await installOperational(page, { role: 'mechanic', routes: [{
    method: 'GET', path: '/api/tracker/issues', handler: request => {
      const query = new URL(request.url).searchParams
      if (query.get('owned_by_me') === 'true') {
        ownedQueries.push(Number(query.get('offset') ?? 0))
        return { json: { items: [owned], total: 1, limit: 50, offset: 0, has_more: false } }
      }
      return { json: { items: [newer, unknown, older], total: 3, limit: 50, offset: 0, has_more: false } }
    },
  }] })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.goto('/work?park=7')
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-OLDER:/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-OWNED:/ })).toBeVisible()
  const order = await page.locator('.rp-work-list-scroll .rp-entity-row').allTextContents()
  expect(order.map(text => text.match(/ROBOPARK-(OLDER|NEWER|UNKNOWN|OWNED)/)?.[0])).toEqual(['ROBOPARK-OLDER', 'ROBOPARK-NEWER', 'ROBOPARK-UNKNOWN', 'ROBOPARK-OWNED'])
  await expect(page.locator('.rp-work-list-scroll .rp-entity-row').nth(2)).toContainText('SLA: —')
  expect(ownedQueries).toEqual([0])
  const firstCard = page.locator('.rp-work-list-scroll .rp-entity-row').first()
  const statusBox = await firstCard.locator('.rp-entity-row__status').boundingBox()
  const actionsBox = await firstCard.locator('.rp-entity-row__actions').boundingBox()
  expect(statusBox && actionsBox).toBeTruthy()
  expect(statusBox!.y + statusBox!.height).toBeLessThanOrEqual(actionsBox!.y)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await settlePage(page)
  await page.screenshot({ path: '/tmp/robopark-work-priority-phone.png', fullPage: true })
  const takeFirst = firstCard.getByRole('button', { name: 'Взять в работу' })
  await takeFirst.evaluate(element => element.scrollIntoView({ block: 'center' }))
  const takeBox = await takeFirst.boundingBox()
  expect(takeBox).toBeTruthy()
  expect(await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.closest('button')?.textContent?.includes('Взять в работу'), {
    x: takeBox!.x + takeBox!.width / 2,
    y: takeBox!.y + takeBox!.height / 2,
  })).toBe(true)

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.emulateMedia({ colorScheme: 'light' })
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
  const desktopStatus = await firstCard.locator('.rp-entity-row__status').boundingBox()
  const desktopActions = await firstCard.locator('.rp-entity-row__actions').boundingBox()
  expect(desktopStatus && desktopActions).toBeTruthy()
  expect(desktopStatus!.y + desktopStatus!.height).toBeLessThanOrEqual(desktopActions!.y)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(1440)
  await settlePage(page)
  await page.screenshot({ path: '/tmp/robopark-work-priority-desktop.png', fullPage: true })
})

test('deep-link restores filters, pagination and detail after reload', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => { const url = new URL(request.url()); if (url.pathname === '/api/tracker/issues') queries.push(url.searchParams) })
  await installOperational(page, { role: 'operator' })
  await page.goto('/work?park=7&status=open&sort=newest&page=2&robot=447&assignee=mechanic.test&age=2')
  await expect(page.getByLabel('Очередь', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('open')
  await expect(page.getByText('От старых к новым', { exact: true })).toBeVisible()
  await expect(page.getByRole('option', { name: 'Сначала новые' })).toHaveCount(0)
  await expect(page.getByText('Робот: 447', { exact: true })).toBeVisible()
  await expect(page.getByText('Ответственный: mechanic.test', { exact: true })).toBeVisible()
  await expect(page.getByText('Старше 2 ч', { exact: true })).toBeVisible()
  await expect(page.getByText('Страница 2', { exact: true })).toBeVisible()
  await expect.poll(() => queries.length).toBeGreaterThan(0)
  expect(Object.fromEntries(queries[0])).toMatchObject({ offset: '50', limit: '50', park: 'north', queue: 'ROBOPARK', status: 'open', open_only: 'true', sort: 'oldest', robot: '447', assignee: 'mechanic.test', age_hours: '2' })
  await page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ }).click()
  const detailUrl = page.url()
  expect(new URL(detailUrl).pathname).toBe('/work/ROBOPARK-42')
  expect(Object.fromEntries(new URL(detailUrl).searchParams)).toMatchObject({ park: '7', status: 'open', page: '2', robot: '447', assignee: 'mechanic.test', age: '2' })
  expect(new URL(detailUrl).searchParams.has('sort')).toBe(false)
  await page.reload()
  await expect(page).toHaveURL(detailUrl)
  await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  await expect(page.getByText('Актуальное состояние задачи пока недоступно; изменения временно заблокированы.', { exact: true })).toBeVisible()
  await expect(page.locator('.issue-actions')).toHaveCount(0)
})

test('viewing-status selector changes the task query while keeping park and oldest-first ordering', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => { const url = new URL(request.url()); if (url.pathname === '/api/tracker/issues') queries.push(url.searchParams) })
  await installOperational(page, { role: 'operator' })
  await page.goto('/work?park=7')

  await expect(page.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('queued')
  await page.getByRole('combobox', { name: 'Статус задач' }).selectOption('diagnostics')
  await expect(page).toHaveURL(/\/work\?park=7&queue=ROBOPARK&status=diagnostics$/)
  await expect.poll(() => queries.some(query => query.get('status') === 'diagnostics' && query.get('sort') === 'oldest' && query.get('open_only') === 'true')).toBe(true)
})

for (const width of [320, 390]) {
  test(`viewing-status selector stays usable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'operator' })
    await page.goto('/work?park=7')

    const filters = page.locator('.rp-work-filters')
    const selector = filters.getByRole('combobox', { name: 'Статус задач' })
    await expect(selector).toBeVisible()
    const filterBox = await filters.boundingBox()
    const selectorBox = await selector.boundingBox()
    expect(filterBox && selectorBox).toBeTruthy()
    expect(selectorBox!.height).toBeGreaterThanOrEqual(44)
    expect(selectorBox!.width).toBeGreaterThanOrEqual(filterBox!.width - 48)
    expect(selectorBox!.x + selectorBox!.width).toBeLessThanOrEqual(filterBox!.x + filterBox!.width)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  })
}

for (const width of [390, 412]) {
  test(`work cards use phone width at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await installOperational(page, { role: 'mechanic' })
    await page.goto('/work?park=7')

    const list = page.locator('.rp-master-detail__list')
    const card = list.locator('.rp-entity-row').first()
    await expect(card).toBeVisible()
    const listBox = await list.boundingBox()
    const cardBox = await card.boundingBox()
    expect(listBox && cardBox).toBeTruthy()
    expect(listBox!.width).toBeGreaterThanOrEqual(width - 32)
    expect(cardBox!.width).toBeGreaterThanOrEqual(listBox!.width - 32)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width)
  })
}

test('phone back restores list scroll and saved filters', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { role: 'operator', listCount: 50 })
  await page.goto('/work?park=7&status=open&sort=newest&page=2')
  const row = page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ })
  await expect(row).toBeVisible()
  await settlePage(page)
  await row.evaluate(element => element.scrollIntoView({ block: 'start' }))
  const saved = await page.evaluate(() => window.scrollY)
  expect(saved).toBeGreaterThan(0)
  await row.click()
  await expect(page.locator('.rp-work-list-pane')).toBeHidden()
  await page.getByRole('button', { name: 'Назад к списку', exact: true }).click()
  await expect(page).toHaveURL(/\/work\?park=7&queue=ROBOPARK&status=open&page=2$/)
  // Firefox can round the fractional scrollIntoView position by one CSS pixel
  // when the button is focused. The restored row and filter must stay in place.
  await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - saved)).toBeLessThanOrEqual(1)
  await expect(row).toBeInViewport()
  await row.click()
  await expect(page).toHaveURL(/\/work\/ROBOPARK-42/)
  await page.goBack()
  await expect(page).toHaveURL(/\/work\?park=7&queue=ROBOPARK&status=open&page=2$/)
  await expect.poll(async () => Math.abs(await page.evaluate(() => window.scrollY) - saved)).toBeLessThanOrEqual(1)
  await expect(row).toBeInViewport()
})

test('attachment capability cannot bypass missing workflow state', async ({ page }) => {
  await installOperational(page, { issue: { ...issue, capabilities: { comment: false, assign: false, unassign: false, transition: false, close: false, attach: true } } })
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByText('Актуальное состояние задачи пока недоступно; изменения временно заблокированы.', { exact: true })).toBeVisible()
  await expect(page.locator('.issue-actions')).toHaveCount(0)
})

test('foreign mechanic detail-check link stays read-only without Emergency requests', async ({ page }) => {
  const emergencyRequests: string[] = []
  const presenceRequests: string[] = []
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (path.startsWith('/api/emergency/')) emergencyRequests.push(request.url())
    if (path.endsWith('/presence')) presenceRequests.push(request.url())
  })
  await installOperational(page, {
    role: 'mechanic',
    issue: { ...issue, assignee: { display: 'Сменщик', login: 'other-mechanic' } },
  })

  await page.goto('/work/ROBOPARK-42?park=7&view=check')

  await expect(page.getByRole('tab', { name: 'Задача', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tab', { name: 'Проверка', exact: true })).toHaveCount(0)
  const robotField = page.getByText('Робот', { exact: true }).locator('..')
  await expect(robotField).toContainText('447')
  await expect(robotField.getByRole('button')).toHaveCount(0)
  await expect(robotField.getByRole('link')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Проверить робота 447', exact: true })).toHaveCount(0)
  await expect(page.getByText(/Для изменений возьмите задачу вместо сменщика/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Закрыть тикет', exact: true })).toHaveCount(0)
  await page.waitForTimeout(2_000)
  expect(emergencyRequests).toEqual([])
  expect(presenceRequests).toEqual([])
})

test('mechanic can inspect an unclaimed queued robot without changing its task', async ({ page }) => {
  const emergencyRequests: string[] = []
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/emergency/')) emergencyRequests.push(request.url())
  })
  await installOperational(page, {
    role: 'mechanic',
    issue: { ...issue, status: 'В очереди', status_key: 'queued', assignee: null },
  })
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Проверка', exact: true })).toBeVisible()
  expect(emergencyRequests).toEqual([])
  await page.getByRole('tab', { name: 'Проверка', exact: true }).click()
  await expect.poll(() => emergencyRequests.length).toBeGreaterThan(0)
  await expect(page.getByRole('button', { name: 'Закрыть тикет', exact: true })).toHaveCount(0)
})

test('robot check failure keeps the mechanic task available and can be retried', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 })
  let resolveCalls = 0
  let available = false
  await installOperational(page, {
    role: 'mechanic',
    issue: { ...issue, status: 'В очереди', status_key: 'queued', assignee: null },
    routes: [{ method: 'POST', path: '/api/emergency/resolve', handler: () => {
      resolveCalls += 1
      return !available
        ? { status: 503, json: { detail: 'emergency_upstream_error' } }
        : { json: { vin: snapshot.vin, sections: [{ id: 'wheels', title: 'Колёса' }] } }
    } }],
  })
  await page.goto('/work/ROBOPARK-42?park=7')
  await page.getByRole('tab', { name: 'Проверка', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Сервис временно недоступен')
  await expect(page.getByRole('button', { name: 'Повторить' })).toBeVisible()
  await page.screenshot({ path: info.outputPath('robot-check-error-phone.png'), fullPage: true })
  available = true
  await page.getByRole('button', { name: 'Повторить' }).click()
  await expect(page.getByRole('region', { name: 'Состояние робота' })).toBeVisible()
  expect(resolveCalls).toBeGreaterThanOrEqual(2)
  await page.getByRole('tab', { name: 'Задача', exact: true }).click()
  await expect(page.getByRole('heading', { name: issue.summary, exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Назад к списку' }).click()
  await expect(page.getByRole('button', { name: 'Взять в работу' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})

test('mechanic can inspect other open and recently closed repairs for the same robot on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const queries: URLSearchParams[] = []
  const previousOpen = { ...issue, key: 'ROBOPARK-43', summary: 'Повторная проблема колеса', status: 'В работе', status_key: 'in_progress' }
  const previousClosed = { ...issue, key: 'ROBOPARK-41', summary: `${issue.summary} by another-mechanic`, status: 'Закрыт', status_key: 'closed' }
  await installOperational(page, { role: 'mechanic', routes: [{
    method: 'GET', path: '/api/tracker/issues', handler: request => {
      const query = new URL(request.url).searchParams
      if (query.get('related_repairs') === 'true') {
        queries.push(query)
        const items = query.get('status') === 'closed' ? [previousClosed] : [previousOpen]
        return { json: { items, total: 1, limit: 10, offset: 0, has_more: false } }
      }
      return { json: { items: [issue], total: 1, limit: 50, offset: 0, has_more: false } }
    },
  }] })
  await page.goto('/work/ROBOPARK-42?park=7')
  await page.getByRole('tab', { name: 'Открытые задачи', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Открытые задачи робота 447' })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-43:/ })).toBeVisible()
  await page.getByRole('tab', { name: 'Закрытые задачи', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Закрытые задачи робота 447' })).toBeVisible()
  await expect(page.getByRole('button', { name: /^Открыть задачу ROBOPARK-41:/ })).toBeVisible()
  await expect(page.locator('.rp-work-related-tasks .rp-work-possible-repeat')).toContainText('Возможный повтор проблемы')
  expect(queries.map(query => ({ robot: query.get('robot_exact'), exclude: query.get('exclude_key'),
    status: query.get('status'), openOnly: query.get('open_only') }))).toEqual([
    { robot: '447', exclude: 'ROBOPARK-42', status: null, openOnly: 'true' },
    { robot: '447', exclude: 'ROBOPARK-42', status: 'closed', openOnly: 'false' },
  ])
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  const pagination = await page.getByRole('navigation', { name: 'Страницы ремонтов' }).boundingBox()
  const bottomNav = await page.locator('.rp-shell__bottom-nav').boundingBox()
  expect(pagination && bottomNav).toBeTruthy()
  expect(pagination!.y + pagination!.height).toBeLessThanOrEqual(bottomNav!.y - 8)
  await page.screenshot({ path: '/tmp/robopark-work-related-closed-phone.png', animations: 'disabled' })
})

test('stale tracker detail never exposes manual close or sends a close request', async ({ page }) => {
  const closes: string[] = []
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/close')) closes.push(request.url())
  })
  await installOperational(page)
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByRole('heading', { name: issue.summary })).toBeVisible()
  await expect(page.getByText('Актуальное состояние задачи пока недоступно; изменения временно заблокированы.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Закрыть тикет', exact: true })).toHaveCount(0)
  await expect(page.getByRole('alertdialog', { name: 'Закрыть задачу?' })).toHaveCount(0)
  expect(closes).toEqual([])
})

test('workflow message and review approval use lifecycle actions without manual status controls', async ({ page }) => {
  const actions: string[] = []
  const timeline: Array<{ id: string; kind: 'user'; author: string; text: string; created_at: string; sync_state: 'saved'; attachments: [] }> = []
  const workflow = { owner: { display: 'Механик смены', login: 'mechanic-e2e' }, review_state: 'pending' as const,
    display_status: 'review' as const, sync_state: 'saved' as const, has_current_cycle_comment: true }
  await installOperational(page, { role: 'operator', issue: { ...issue, workflow }, routes: [
    { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/timeline', handler: () => ({ json: timeline }) },
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/messages', handler: async request => {
      actions.push('message')
      const { text } = await request.json() as { text: string }
      const message = { id: 'message-1', kind: 'user' as const, author: 'operator.test', text, created_at: FIXED_TIME, sync_state: 'saved' as const, attachments: [] as [] }
      timeline.push(message)
      return { json: message }
    } },
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/review/approve', handler: () => {
      actions.push('approve')
      return { json: { key: issue.key, action: 'approve-review', status: 'Закрыт', actor: 'operator.test', performed_at: FIXED_TIME, sync_state: 'saved', workflow: { ...workflow, review_state: 'closed', display_status: 'closed' } } }
    } },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')
  await page.getByRole('button', { name: 'История и сообщения' }).click()
  await page.getByRole('textbox', { name: 'Комментарии', exact: true }).fill('Проверка колеса выполнена')
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expect(page.getByText('Проверка колеса выполнена', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Решить', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Закрыть тикет', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Принять и закрыть' }).click()
  await expect(page).toHaveURL(/\/work\?park=7/)
  expect(actions).toEqual(['message', 'approve'])
})

test('empty 200 response offers filter recovery', async ({ page }) => {
  await installOperational(page, { routes: [{ method: 'GET', path: '/api/tracker/issues', handler: () => ({ json: { items: [], total: 0, limit: 50, offset: 0, has_more: false } satisfies Paged<TrackerIssueDetail> }) }] })
  await page.goto('/work?park=7')
  await expect(page.getByRole('heading', { name: 'Очередь свободна', exact: true })).toBeVisible()
  await expect(page.getByText('Сейчас в очереди нет задач. Можно открыть задачи в ремонте и ожидании.', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Показать все открытые задачи', exact: true })).toBeVisible()
})

for (const failure of [
  { status: 409, detail: 'tasks_disabled_for_park', title: 'Данные изменились', text: 'Задачи отключены: проверьте очередь Startrek и флаг blockers у парка.' },
  { status: 403, detail: 'tracker_issue_out_of_scope', title: 'Нет доступа', text: 'Тикет вне вашей очереди или парка.' },
  { status: 404, detail: 'not_found', title: 'Не найдено', text: 'Действие недоступно — перезапустите API или обновите страницу.' },
  { status: 502, detail: 'tracker_upstream_error', title: 'Сервис временно недоступен', text: 'Ошибка интеграции со Startrek.' },
  { status: 504, detail: 'gateway_timeout', title: 'Сервис временно недоступен', text: 'Не удалось загрузить очередь задач.' },
]) {
  test(`${failure.status} ${failure.detail}: safe recovery and request ID`, async ({ page }) => {
    const requestId = `work-${failure.status}-42`
    await installOperational(page, { routes: [{ method: 'GET', path: '/api/tracker/issues', handler: () => ({ status: failure.status, headers: { 'x-request-id': requestId }, json: { detail: failure.detail } }) }] })
    await page.goto('/work?park=7')
    await expect(page.getByRole('heading', { name: failure.title, exact: true })).toBeVisible()
    await expect(page.getByText(failure.text, { exact: true })).toBeVisible()
    await expect(page.getByText(`Код запроса: ${requestId}`, { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Повторить', exact: true })).toHaveCount(failure.status === 403 || failure.status === 404 ? 0 : 1)
  })
}


test('work list defaults to the oldest queued tasks with a viewing-only status selector', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname === '/api/tracker/issues') queries.push(url.searchParams)
  })
  await installOperational(page)
  await page.goto('/work?park=7&queue=OBSOLETE&sort=newest&page=2')
  const filters = page.locator('.rp-work-filters')
  await expect(filters).toContainText('В очереди')
  await expect(filters).toContainText('От старых к новым')
  await expect(filters.locator('input')).toHaveCount(0)
  await expect(filters.getByRole('combobox', { name: 'Статус задач' })).toHaveValue('queued')
  await expect(filters.getByRole('button')).toHaveCount(0)
  await expect.poll(() => Object.fromEntries(queries.find(query => query.get('queue') === 'ROBOPARK')!)).toMatchObject({
    queue: 'ROBOPARK', park: 'north', status: 'queued', open_only: 'true', sort: 'oldest', offset: '50',
  })
  await page.reload()
  await expect(filters).toContainText('В очереди')
  await expect.poll(() => queries.filter(query => query.get('queue') === 'ROBOPARK').at(-1)?.get('status')).toBe('queued')
})
