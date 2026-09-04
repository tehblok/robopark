import { expect, test } from '@playwright/test'
import type { Paged, TrackerActionResult, TrackerIssueDetail } from '../../src/api'
import { FIXED_TIME, installOperational, issue, operationalRoutes, settlePage } from './fixtures'

test('deep-link restores filters, pagination and detail after reload', async ({ page }) => {
  const queries: URLSearchParams[] = []
  page.on('request', request => { const url = new URL(request.url()); if (url.pathname === '/api/tracker/issues') queries.push(url.searchParams) })
  await installOperational(page, { role: 'operator' })
  await page.goto('/work?park=7&status=open&sort=newest&page=2&robot=447&assignee=mechanic.test&age=2')
  await expect(page.getByLabel('Очередь', { exact: true })).toHaveValue('ROBOPARK')
  await expect(page.getByLabel('Статус', { exact: true })).toHaveValue('open')
  await expect(page.getByText('Сначала старые', { exact: true })).toBeVisible()
  await expect(page.getByRole('option', { name: 'Сначала новые' })).toHaveCount(0)
  await expect(page.getByLabel('Робот', { exact: true })).toHaveValue('447')
  await expect(page.getByLabel('Ответственный', { exact: true })).toHaveValue('mechanic.test')
  await expect(page.getByLabel('Старше, часов')).toHaveValue('2')
  await expect(page.getByText('Страница 2', { exact: true })).toBeVisible()
  await expect.poll(() => queries.length).toBeGreaterThan(0)
  expect(Object.fromEntries(queries[0])).toMatchObject({ offset: '50', limit: '50', park: 'north', queue: 'ROBOPARK', status: 'open', sort: 'oldest', robot: '447', assignee: 'mechanic.test', age_hours: '2' })
  await page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ }).click()
  const detailUrl = page.url()
  expect(new URL(detailUrl).pathname).toBe('/work/ROBOPARK-42')
  expect(Object.fromEntries(new URL(detailUrl).searchParams)).toMatchObject({ park: '7', status: 'open', page: '2', robot: '447', assignee: 'mechanic.test', age: '2' })
  expect(new URL(detailUrl).searchParams.has('sort')).toBe(false)
  await page.reload()
  await expect(page).toHaveURL(detailUrl)
  await expect(page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Закрыть тикет', exact: true })).toBeVisible()
})

test('phone back restores list scroll and saved filters', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await installOperational(page, { listCount: 50 })
  await page.goto('/work?park=7&status=open&sort=newest&page=2')
  const row = page.getByRole('button', { name: /^Открыть задачу ROBOPARK-42:/ })
  await expect(row).toBeVisible()
  await settlePage(page)
  await row.scrollIntoViewIfNeeded()
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

test('attachment-only capabilities expose only the attachment mutation', async ({ page }) => {
  await installOperational(page, { issue: { ...issue, capabilities: { comment: false, assign: false, unassign: false, transition: false, close: false, attach: true } } })
  await page.goto('/work/ROBOPARK-42?park=7')
  const actions = page.locator('.issue-actions')
  await expect(actions.getByRole('button', { name: 'Выбрать фото', exact: true })).toBeVisible()
  await expect(actions.getByRole('button', { name: /Закрыть тикет|Решить|Назначить|Снять исполнителя|Отправить/ })).toHaveCount(0)
  await expect(actions.locator('textarea')).toHaveCount(0)
  await expect(actions.getByLabel('Логин исполнителя')).toHaveCount(0)
})

test('close requires confirmation, preserves failure, then closes and refreshes local resources', async ({ page }) => {
  let closes = 0
  const reads: string[] = []
  page.on('request', request => { if (request.method() === 'GET' && new URL(request.url()).pathname.startsWith('/api/tracker/')) reads.push(new URL(request.url()).pathname) })
  await installOperational(page, { routes: [{ method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/close', handler: () => {
    closes += 1
    return closes === 1
      ? { status: 409, headers: { 'x-request-id': 'close-conflict-42' }, json: { detail: 'tracker_transition_invalid' } }
      : { json: { key: issue.key, action: 'close', status: 'ok', actor: 'mechanic.test', performed_at: FIXED_TIME } satisfies TrackerActionResult }
  } }] })
  await page.goto('/work/ROBOPARK-42?park=7')
  await page.getByRole('button', { name: 'Закрыть тикет', exact: true }).click()
  const dialog = page.getByRole('alertdialog', { name: 'Закрыть задачу?' })
  await expect(dialog).toBeVisible()
  expect(closes).toBe(0)
  await dialog.getByRole('button', { name: 'Отмена', exact: true }).click()
  expect(closes).toBe(0)
  await page.getByRole('button', { name: 'Закрыть тикет', exact: true }).click()
  await dialog.getByRole('button', { name: 'Подтвердить закрытие' }).click()
  await expect(dialog.getByRole('alert')).toContainText('Этот переход недоступен для тикета.')
  await expect(dialog.getByRole('alert')).toContainText('close-conflict-42')
  await expect(page).toHaveURL(/\/work\/ROBOPARK-42/)
  const before = reads.length
  await dialog.getByRole('button', { name: 'Подтвердить закрытие' }).click()
  await expect(dialog).toBeHidden()
  await expect(page).toHaveURL(/\/work\?park=7/)
  await expect.poll(() => reads.slice(before)).toEqual(expect.arrayContaining(['/api/tracker/issues', '/api/tracker/issues/ROBOPARK-42', '/api/tracker/issues/ROBOPARK-42/comments', '/api/tracker/transitions/ROBOPARK-42']))
  expect(closes).toBe(2)
})

test('comment, assignment, transition and attachment use their complete action contracts', async ({ page }) => {
  const actions: string[] = []
  const routes = operationalRoutes()
  for (const route of routes) {
    if (route.method !== 'POST' || !String(route.path).includes('tracker')) continue
    const handler = route.handler
    route.handler = request => { actions.push(new URL(request.url).pathname.split('/').at(-1)!); return handler(request) }
  }
  await installOperational(page, { routes })
  await page.goto('/work/ROBOPARK-42?park=7')
  await page.getByRole('textbox', { name: 'Комментарии', exact: true }).fill('Проверка колеса выполнена')
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expect(page.getByText('Проверка колеса выполнена', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Назначить на себя', exact: true }).click()
  await expect.poll(() => actions).toContain('assign')
  await page.getByRole('button', { name: 'Снять исполнителя', exact: true }).click()
  await expect.poll(() => actions).toContain('unassign')
  await page.getByRole('button', { name: 'Решить', exact: true }).click()
  await expect(page.locator('.rp-work-detail-pane').getByText('Закрыт', { exact: true })).toBeVisible()
  await page.locator('.issue-actions input[type=file]').setInputFiles({ name: 'wheel.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jA/0AAAAASUVORK5CYII=', 'base64') })
  await page.getByRole('button', { name: 'Прикрепить', exact: true }).click()
  await expect.poll(() => actions).toEqual(['comment', 'assign', 'unassign', 'transition', 'attachments'])
  await expect(page.locator('.issue-attachments')).toContainText('wheel.png')
  await expect(page.locator('.issue-attach-preview')).toHaveCount(0)
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
