import { expect, test } from '@playwright/test'
import type { Paged, TrackerIssueDetail } from '../../src/api'
import { FIXED_TIME, installOperational, issue, settlePage } from './fixtures'

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
  await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
  await expect(page.getByText(/Обновите страницу.*действия временно недоступны/)).toBeVisible()
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
    expect(cardBox!.width).toBeGreaterThanOrEqual(width - 60)
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
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(saved)
  await row.click()
  await expect(page).toHaveURL(/\/work\/ROBOPARK-42/)
  await page.goBack()
  await expect(page).toHaveURL(/\/work\?park=7&queue=ROBOPARK&status=open&page=2$/)
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(saved)
})

test('attachment capability cannot bypass missing workflow state', async ({ page }) => {
  await installOperational(page, { issue: { ...issue, capabilities: { comment: false, assign: false, unassign: false, transition: false, close: false, attach: true } } })
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByText(/Обновите страницу.*действия временно недоступны/)).toBeVisible()
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
  await expect(page.getByRole('tab', { name: 'Проверка робота', exact: true })).toHaveCount(0)
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

test('stale tracker detail never exposes manual close or sends a close request', async ({ page }) => {
  const closes: string[] = []
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/close')) closes.push(request.url())
  })
  await installOperational(page)
  await page.goto('/work/ROBOPARK-42?park=7')
  await expect(page.getByRole('heading', { name: issue.summary })).toBeVisible()
  await expect(page.getByText(/Обновите страницу.*действия временно недоступны/)).toBeVisible()
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
  await expect(page.getByRole('heading', { name: 'Нет задач', exact: true })).toBeVisible()
  await expect(page.getByText('Измените фильтры или проверьте выбранный парк.')).toBeVisible()
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
