import { expect, test, type Page } from '@playwright/test'
import type { User } from '../../src/api'
import type { MockResponse, MockRoute } from '../support/mockApi'
import { startDiagnosticApi } from '../support/diagnosticApi'
import { installOperational, userForRole } from './fixtures'

type Actor = 'mechanic' | 'mechanic-next' | 'operator'
type Bridge = Awaited<ReturnType<typeof startDiagnosticApi>>
type Session = { actor: Actor; signedIn: boolean }

const PHOTO = { name: 'wheel.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jA/0AAAAASUVORK5CYII=', 'base64') }
const DEFECT_FIELD = '60df26695151a36df681d67b--theDefectCode'

function browserUser(actor: Actor): User {
  const user = userForRole(actor === 'operator' ? 'operator' : 'mechanic')
  return { ...user, id: actor === 'mechanic-next' ? 202 : user.id, username: `${actor}-browser`, tracker_login: `${actor}-browser` }
}

async function bridgeCall(bridge: Bridge, input: Record<string, unknown>, actor: Actor = 'mechanic') {
  return (bridge.call as unknown as (input: Record<string, unknown>, actor: Actor) => Promise<MockResponse>)(input, actor)
}

function trackerRoutes(bridge: Bridge, session: Session): MockRoute[] {
  const handler = async (request: Request) => {
    const url = new URL(request.url)
    const body = ['GET', 'HEAD'].includes(request.method) ? undefined : Buffer.from(await request.arrayBuffer()).toString('base64')
    return bridgeCall(bridge, { method: request.method, path: url.pathname.replace(/^\/api/, '') + url.search, body_base64: body, headers: Object.fromEntries(request.headers) }, session.actor)
  }
  return (['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const).map(method => ({ method, path: /^\/api\/tracker(?:\/|$)/, handler }))
}

function authRoutes(session: Session): MockRoute[] {
  return [
    { method: 'GET', path: '/api/auth/me', handler: () => session.signedIn ? { json: browserUser(session.actor) } : { status: 401, json: { detail: 'Unauthorized' } } },
    { method: 'POST', path: '/api/auth/login', handler: async (request) => {
      const username = String((await request.json() as { username?: string }).username ?? '')
      const candidate = (['mechanic-browser', 'mechanic-next-browser', 'operator-browser'] as const).find(item => item === username)
      if (!candidate) return { status: 401, json: { detail: 'invalid_credentials' } }
      session.actor = candidate.replace('-browser', '') as Actor
      session.signedIn = true
      return { status: 204 }
    } },
    { method: 'POST', path: '/api/auth/logout', handler: () => { session.signedIn = false; return { status: 204 } } },
  ]
}

async function installLifecycle(page: Page, bridge: Bridge, session: Session) {
  await installOperational(page, { user: browserUser(session.actor), routes: [...authRoutes(session), ...trackerRoutes(bridge, session)] })
}

async function snapshot(bridge: Bridge) {
  const response = await bridgeCall(bridge, { control: 'snapshot' })
  return response.json as { counts: Record<string, number>; field_values: Record<string, string>; actions: Array<{ id: string; action: string; state: string }>; timeline: string[] }
}

async function drain(bridge: Bridge) {
  expect((await bridgeCall(bridge, { control: 'drain' })).status).toBe(200)
}

async function openIssue(page: Page) {
  if (await page.getByRole('heading', { name: /Проверить колесо робота/ }).isVisible().catch(() => false)) return
  const overviewLink = page.getByRole('link', { name: 'Открыть задачу ROBOPARK-42' }).first()
  const workButton = page.getByRole('button', { name: /Открыть задачу ROBOPARK-42/ })
  await expect(overviewLink.or(workButton)).toBeVisible()
  if (await overviewLink.isVisible().catch(() => false)) await overviewLink.click()
  else await workButton.click()
  await expect(page.getByRole('heading', { name: /Проверить колесо робота/ })).toBeVisible()
}

async function switchUser(page: Page, username: `${Actor}-browser`) {
  await page.getByRole('button', { name: 'Ещё', exact: true }).click()
  await page.getByRole('button', { name: 'Выйти', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Вход' })).toBeVisible()
  await page.getByRole('textbox', { name: 'Логин' }).fill(username)
  await page.getByRole('textbox', { name: 'Пароль', exact: true }).fill('public-fixture-password')
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.getByText(username, { exact: true })).toBeVisible()
  await openIssue(page)
}

async function comment(page: Page, text: string) {
  await page.getByRole('textbox', { name: 'Комментарии', exact: true }).fill(text)
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expect(page.getByText(text, { exact: true })).toBeVisible()
}

async function handoff(page: Page, assignee: string, reason: string) {
  await page.getByRole('group', { name: 'Дополнительные разделы задачи' }).getByRole('button', { name: 'Передать смену', exact: true }).click()
  await page.getByLabel('Логин сменщика').fill(assignee)
  await page.getByLabel('Причина передачи').fill(reason)
  await page.getByRole('button', { name: 'Передать смену', exact: true }).last().click()
  await expect(page.getByText(reason, { exact: false })).toBeVisible()
}

async function submitReview(page: Page, input: { clarification?: string; camera?: boolean }) {
  await page.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
  const form = page.locator('form').filter({ has: page.getByLabel('Код дефекта') })
  await form.getByLabel('Код дефекта').fill('BD-01')
  if (input.clarification) await form.getByRole('textbox', { name: /Добавить уточнение|Комментарий о выполненной работе/ }).fill(input.clarification)
  await (input.camera ? form.getByLabel('Сделать фото') : form.getByLabel('Выбрать файл')).setInputFiles(PHOTO)
  await expect(form.getByRole('img', { name: 'Предпросмотр wheel.png' })).toBeVisible()
  await form.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
  await expect(form).not.toBeVisible()
}

async function returnReview(page: Page) {
  page.once('dialog', dialog => dialog.accept('Повторить проверку'))
  await page.getByRole('button', { name: 'Вернуть в работу', exact: true }).click()
  await expect(page.getByText('Повторить проверку', { exact: false })).toBeVisible()
}

async function assertMobileContract(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  for (const control of await page.locator('button:visible, label.btn:visible').all()) expect((await control.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44)
  await expect(page.locator('main')).toHaveAttribute('id', 'main-content')
}

async function runLifecycle(page: Page, width: number) {
  const bridge = await startDiagnosticApi()
  const session: Session = { actor: 'mechanic', signedIn: true }
  const mobile = width === 390
  try {
    await installLifecycle(page, bridge, session)
    await page.setViewportSize({ width, height: mobile ? 844 : 900 })
    await page.goto('/work?park=7')

    if (mobile) await bridgeCall(bridge, { control: 'tracker', available: false })
    await page.getByRole('button', { name: 'Взять в работу', exact: true }).dblclick()
    await expect(page.getByRole('heading', { name: /Проверить колесо робота/ })).toBeVisible()
    if (mobile) {
      await drain(bridge)
      expect((await snapshot(bridge)).actions.some(action => action.state === 'retry_wait')).toBe(true)
      await bridgeCall(bridge, { control: 'tracker', available: true })
    }
    await drain(bridge)
    await page.reload()

    await comment(page, 'Заменено крепление колеса')
    await handoff(page, 'mechanic-next-browser', 'Конец смены')
    await switchUser(page, 'mechanic-next-browser')
    await handoff(page, 'mechanic-browser', 'Проверка второй сменой завершена')
    await switchUser(page, 'mechanic-browser')
    await comment(page, 'Проверено после передачи')
    await submitReview(page, { camera: mobile })
    await drain(bridge)

    await switchUser(page, 'operator-browser')
    await returnReview(page)
    await drain(bridge)
    await switchUser(page, 'mechanic-browser')
    await comment(page, 'Исправлено после возврата')
    await submitReview(page, { clarification: 'Уточнение после возврата', camera: mobile })
    await drain(bridge)

    await switchUser(page, 'operator-browser')
    await page.getByRole('button', { name: 'Принять и закрыть', exact: true }).click()
    await drain(bridge)

    await openIssue(page)
    const timeline = page.getByRole('region', { name: 'Чат задачи' }).locator('.task-message .issue-comment-text')
    const expected = ['Задача взята в работу', 'Заменено крепление колеса', 'Конец смены', 'Проверка второй сменой завершена', 'Проверено после передачи', 'Передано на проверку', 'Повторить проверку', 'Исправлено после возврата', 'Уточнение после возврата', 'Передано на проверку']
    await expect.poll(async () => {
      const texts = await timeline.allTextContents()
      let cursor = -1
      return expected.every(fragment => { cursor = texts.findIndex((text, index) => index > cursor && text.includes(fragment)); return cursor >= 0 })
    }).toBe(true)

    const evidence = await snapshot(bridge)
    expect(evidence.field_values[DEFECT_FIELD]).toBe('BD-01')
    expect(evidence.counts.upload).toBe(2)
    expect(evidence.counts[`field:${DEFECT_FIELD}`]).toBe(2)
    expect(evidence.counts['transition:start']).toBe(1)
    expect(evidence.counts['transition:review']).toBe(2)
    expect(evidence.counts['transition:return']).toBe(1)
    expect(evidence.counts['transition:close']).toBe(1)
    const commentActions = evidence.actions.filter(action => action.action === 'comment')
    const attachmentActions = evidence.actions.filter(action => action.action === 'attach')
    expect(commentActions).toHaveLength(7)
    expect(attachmentActions).toHaveLength(2)
    const expectedCommentKeys = [...commentActions, ...attachmentActions]
      .map(action => `comment:surp-action:${action.id}`)
      .sort()
    const deliveredComments = Object.fromEntries(Object.entries(evidence.counts)
      .filter(([key]) => key.startsWith('comment:surp-action:')))
    expect(deliveredComments).toEqual(Object.fromEntries(expectedCommentKeys.map(key => [key, 1])))
    expect(new Set(evidence.actions.map(action => action.id)).size).toBe(evidence.actions.length)
    expect(evidence.actions.every(action => action.state === 'succeeded')).toBe(true)
    if (mobile) await assertMobileContract(page)
  } finally {
    await bridge.close()
  }
}

test.beforeEach(() => { process.env.DIAGNOSTIC_E2E_MUTATION = 'task-lifecycle' })
test.afterEach(() => { delete process.env.DIAGNOSTIC_E2E_MUTATION })

test('desktop completes the full lifecycle through visible controls exactly once', async ({ page }) => {
  test.setTimeout(30_000)
  await runLifecycle(page, 1440)
})

test('390x844 completes the full lifecycle, outage recovery and mobile contract', async ({ page }) => {
  test.setTimeout(30_000)
  await runLifecycle(page, 390)
})
