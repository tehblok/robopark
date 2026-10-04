import AxeBuilder from '@axe-core/playwright'
import type { Page } from '@playwright/test'
import { expect, test } from '../support/persistentWebKit'
import { startHttpFixture } from '../support/httpFixture'
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
    if (!session.signedIn) return { status: 401, json: { detail: 'Unauthorized' } }
    const actor = session.actor
    const url = new URL(request.url)
    const body = ['GET', 'HEAD'].includes(request.method) ? undefined : Buffer.from(await request.arrayBuffer()).toString('base64')
    return bridgeCall(bridge, { method: request.method, path: url.pathname.replace(/^\/api/, '') + url.search, body_base64: body, headers: Object.fromEntries(request.headers) }, actor)
  }
  return (['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const).map(method => ({ method, path: /^\/api\/(?:tracker|sync|media)(?:\/|$)/, handler }))
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

async function installLifecycle(page: Page, session: Session, origin: string) {
  await installOperational(page, { user: browserUser(session.actor), routes: authRoutes(session) })
  // Real HTTP preserves 304 responses and multipart bytes: WebKit's route.fulfill
  // rejects 304 and its intercepted multipart body is empty.
  await page.route(/^https?:\/\/[^/]+\/api\/(?:tracker|sync|media)(?:\/|$)/, route => {
    const url = new URL(route.request().url())
    return route.continue({ url: `${origin}${url.pathname}${url.search}` })
  })
}

async function snapshot(bridge: Bridge) {
  const response = await bridgeCall(bridge, { control: 'snapshot' })
  return response.json as { component_ids: string[]; counts: Record<string, number>; field_values: Record<string, string | string[]>; actions: Array<{ id: string; action: string; state: string; error_code: string | null }>; claims: Array<{ issue_key: string; owner_user_id: number; state: string; start_action_id: string }>; timeline: string[] }
}

async function drain(bridge: Bridge) {
  expect((await bridgeCall(bridge, { control: 'drain' })).status).toBe(200)
}

async function waitForServerMessage(bridge: Bridge, fragment: string, occurrence = 1) {
  await expect.poll(
    async () => (await snapshot(bridge)).timeline.filter(text => text.includes(fragment)).length,
    { timeout: 15_000 },
  ).toBeGreaterThanOrEqual(occurrence)
  await drain(bridge)
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

async function switchUser(page: Page, username: `${Actor}-browser`, password: string) {
  await page.getByRole('button', { name: /^(?:Ещё|Меню)$/ }).click()
  const logoutResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/logout' && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Выйти', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Вход' })).toBeVisible()
  expect((await logoutResponse).status()).toBe(204)
  expect(await page.evaluate(async () => (await fetch('/api/tracker/issues')).status)).toBe(401)
  await page.getByRole('textbox', { name: 'Логин' }).fill(username)
  await page.getByRole('textbox', { name: 'Пароль', exact: true }).fill(password)
  await page.getByRole('button', { name: 'Войти', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Вход' })).toBeHidden()
  const mobileMenu = page.getByRole('button', { name: 'Меню', exact: true })
  if ((page.viewportSize()?.width ?? 1440) <= 899) {
    await expect(mobileMenu).toBeVisible()
    await mobileMenu.click()
    const menu = page.getByRole('dialog', { name: 'Меню', exact: true })
    await expect(menu).toHaveAccessibleDescription(new RegExp(username))
    await menu.getByRole('button', { name: 'Закрыть', exact: true }).click()
  } else {
    await expect(page.locator('.rp-shell__topbar .rp-shell__user').getByText(username, { exact: true })).toBeVisible()
  }
  await openIssue(page)
}

async function openTaskConversation(page: Page) {
  const conversation = page.getByRole('button', { name: 'История и сообщения', exact: true })
  if (await conversation.getAttribute('aria-expanded') !== 'true') await conversation.click()
}

async function comment(page: Page, text: string) {
  await openTaskConversation(page)
  await page.getByRole('textbox', { name: 'Комментарии', exact: true }).fill(text)
  await page.getByRole('button', { name: 'Отправить', exact: true }).click()
  await expectChatText(page, text)
}

async function expectChatText(page: Page, text: string) {
  await openTaskConversation(page)
  await expect(page.getByRole('region', { name: 'Чат задачи' }).getByText(text, { exact: false })).toBeVisible()
}

async function handoff(page: Page, assignee: string, reason: string) {
  await page.getByRole('tab', { name: 'Задача', exact: true }).click()
  await page.getByRole('group', { name: 'Дополнительные разделы задачи' }).getByRole('button', { name: 'Передать смену', exact: true }).click()
  await page.getByLabel('Логин сменщика').fill(assignee)
  await page.getByLabel('Причина передачи').fill(reason)
  await page.getByRole('button', { name: 'Передать смену', exact: true }).last().click()
  await expectChatText(page, reason)
}

async function submitReview(page: Page, input: { clarification?: string; camera?: boolean; method?: string }) {
  await page.getByRole('tab', { name: 'Задача', exact: true }).click()
  await page.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
  const form = page.locator('form').filter({ has: page.getByLabel('Что случилось?') })
  const note = form.getByRole('textbox', { name: /Добавить уточнение|Комментарий о выполненной работе/ })
  await form.getByLabel('Сделать фото или выбрать файл').setInputFiles(PHOTO)
  if (!input.method) {
    await note.fill('Заменил мотор-колесо BD-01')
    const hints = form.getByRole('region', { name: 'Подсказки по тексту' })
    await expect(hints).toBeVisible()
    await expect(form.getByLabel('Что случилось?')).toHaveValue('')
    await expect(form.getByRole('button', { name: 'Заменил', exact: true })).toHaveAttribute('aria-pressed', 'false')
    await hints.getByText(/почему этот вариант/).first().click()
    expect((await new AxeBuilder({ page }).include('form').analyze()).violations).toEqual([])
    // Hide fixed shell chrome only in the artifact: it overlaps an element capture
    // while Playwright scrolls a form taller than the viewport into view.
    await form.screenshot({ path: test.info().outputPath('repair-prefill-preview.png'), style: '.rp-shell__topbar, .rp-shell__bottom-nav { visibility: hidden !important; }' })
    await hints.getByRole('button', { name: 'Подставить поля', exact: true }).click()
    await expect(form.getByLabel('Что случилось?')).toHaveValue('BD-01')
    await expect(form.getByRole('button', { name: 'Заменил', exact: true })).toHaveAttribute('aria-pressed', 'true')
    await form.getByRole('button', { name: 'Отменить подстановку', exact: true }).click()
    await expect(form.getByLabel('Что случилось?')).toHaveValue('')
    await expect(note).toHaveValue('Заменил мотор-колесо BD-01')
    await expect(form.getByRole('img', { name: 'Предпросмотр wheel.png' })).toBeVisible()
    await hints.getByRole('button', { name: 'Подставить поля', exact: true }).click()
  } else {
    await form.getByLabel('Что случилось?').selectOption('BD-01')
    await form.getByRole('button', { name: input.method, exact: true }).click()
    await note.fill(input.clarification ?? '')
  }
  await expect(form.getByText(/Что ремонтируем: Мотор-колесо/)).toBeVisible()
  await expect(form.getByRole('checkbox', { name: /ROBOT_UNSORTED/ })).toHaveCount(0)
  await expect(form.getByRole('img', { name: 'Предпросмотр wheel.png' })).toBeVisible()
  expect((await new AxeBuilder({ page }).include('form').analyze()).violations).toEqual([])
  await form.screenshot({ path: test.info().outputPath(`repair-form-${input.method ? 'returned' : 'initial'}.png`) })
  await form.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
  await expect(form).not.toBeVisible()
}

async function returnReview(page: Page) {
  await page.getByRole('tab', { name: 'Задача', exact: true }).click()
  await page.getByRole('button', { name: 'Вернуть в работу', exact: true }).click()
  await page.getByRole('textbox', { name: 'Что нужно исправить' }).fill('Повторить проверку')
  await page.getByRole('button', { name: 'Вернуть задачу', exact: true }).click()
  await expectChatText(page, 'Повторить проверку')
}

async function assertMobileContract(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  // Firefox can expose an exact 44 CSS px box as 43.999877… through DOMRect.
  const cssPixelEpsilon = 0.001
  for (const control of await page.locator('button:visible, label.btn:visible').all()) expect((await control.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44 - cssPixelEpsilon)
  await expect(page.locator('main')).toHaveAttribute('id', 'main-content')
}

async function runLifecycle(page: Page, width: number) {
  const bridge = await startDiagnosticApi()
  const session: Session = { actor: 'mechanic', signedIn: true }
  const mobile = width === 390
  let tearingDown = false
  const handlers = trackerRoutes(bridge, session)
  const syncExchanges: Array<Record<string, unknown>> = []
  const http = await startHttpFixture(async request => {
    if (tearingDown) return new Response(null, { status: 503 })
    const handler = handlers.find(item => item.method === request.method)
    if (!handler) return new Response(null, { status: 405 })
    const isSyncBatch = new URL(request.url).pathname === '/api/sync/batch'
    const exchange: Record<string, unknown> = { at: Date.now(), actor: session.actor }
    if (isSyncBatch) {
      syncExchanges.push(exchange)
      if (syncExchanges.length > 100) syncExchanges.shift()
    }
    const response = await handler.handler(request)
    if (isSyncBatch) {
      exchange.status = response.status ?? 200
      const result = response.json as { results?: unknown } | undefined
      exchange.results = result?.results
    }
    const status = response.status ?? 200
    const headers = new Headers(response.headers)
    // The bridge returns decoded bytes; framing belongs to this HTTP server.
    headers.delete('content-length')
    headers.delete('content-encoding')
    const body = response.body_base64 !== undefined
      ? Buffer.from(response.body_base64, 'base64')
      : response.json !== undefined ? JSON.stringify(response.json) : response.body ?? null
    return new Response([204, 205, 304].includes(status) || request.method === 'HEAD' ? null : body, { status, headers })
  })
  try {
    // Page routes own all traffic during the test. Only abort attachment loads
    // after teardown starts, when the page-scoped route is being removed.
    await page.context().route(
      /^https?:\/\/[^/]+\/api\/tracker\/issues\/[^/]+\/attachments\/[^/]+\/content$/,
      route => tearingDown ? route.abort() : route.fallback(),
    )
    await installLifecycle(page, session, http.origin)
    await page.setViewportSize({ width, height: mobile ? 844 : 900 })
    await page.goto('/work?park=7')
    await expect(page.getByRole('button', { name: 'Взять в работу', exact: true })).toBeVisible()

    if (mobile) {
      await bridgeCall(bridge, { control: 'tracker', available: false })
      await page.getByRole('button', { name: 'Взять в работу', exact: true }).click()
      await expect(page.getByRole('status').filter({ hasText: 'Взятие ожидает подтверждения' })).toBeVisible()
      expect((await snapshot(bridge)).actions).toHaveLength(0)
      await bridgeCall(bridge, { control: 'tracker', available: true })
      await page.reload()
    } else await page.getByRole('button', { name: 'Взять в работу', exact: true }).dblclick()
    await expect.poll(
      async () => (await snapshot(bridge)).actions.some(action => action.action === 'start'),
      { timeout: 15_000 },
    ).toBe(true)
    await openIssue(page)
    if (mobile) {
      await bridgeCall(bridge, { control: 'tracker', available: false })
      await drain(bridge)
      expect((await snapshot(bridge)).actions.some(action => action.state === 'retry_wait')).toBe(true)
      await bridgeCall(bridge, { control: 'tracker', available: true })
    }
    const pendingClaimSnapshot = await snapshot(bridge)
    expect(pendingClaimSnapshot.claims).toEqual([
      expect.objectContaining({ issue_key: 'ROBOPARK-42', state: 'pending' }),
    ])
    await page.reload()
    const pendingClaim = page.getByText(/Tracker ещё подтверждает взятие задачи/)
    const submitForReview = page.getByRole('button', { name: 'Передать на проверку', exact: true })
    await expect(pendingClaim.or(submitForReview)).toBeVisible()
    if (await pendingClaim.isVisible()) await expect(submitForReview).toHaveCount(0)
    await drain(bridge)
    await expect(submitForReview).toBeVisible({ timeout: 12_000 })
    expect((await snapshot(bridge)).component_ids).toEqual(['162206'])
    const afterClaim = (await snapshot(bridge)).actions
    expect(afterClaim.filter(action => action.state !== 'succeeded')).toEqual([])
    expect(afterClaim.some(action => action.action === 'assign_operator')).toBe(false)

    await comment(page, 'Заменено крепление колеса')
    await handoff(page, 'mechanic-next-browser', 'Конец смены')
    await waitForServerMessage(bridge, 'Конец смены')
    await switchUser(page, 'mechanic-next-browser', bridge.password)
    await handoff(page, 'mechanic-browser', 'Проверка второй сменой завершена')
    await waitForServerMessage(bridge, 'Проверка второй сменой завершена')
    await switchUser(page, 'mechanic-browser', bridge.password)
    await comment(page, 'Проверено после передачи')
    await waitForServerMessage(bridge, 'Проверено после передачи')
    const unavailableOptions = '**/api/tracker/issues/ROBOPARK-42/repair-options'
    if (mobile) await page.route(unavailableOptions, route => route.fulfill({ status: 503, json: { detail: 'tracker_upstream_error' } }))
    await page.reload()
    // Mobile reload restores scoped cached options even while the catalog is down.
    await submitReview(page, { camera: mobile })
    if (mobile) await page.unroute(unavailableOptions)
    await waitForServerMessage(bridge, 'Передано на проверку')
    await drain(bridge)
    expect((await snapshot(bridge)).actions.filter(action => action.action === 'assign_operator'))
      .toEqual([expect.objectContaining({ state: 'succeeded' })])

    await switchUser(page, 'operator-browser', bridge.password)
    await returnReview(page)
    await drain(bridge)
    await switchUser(page, 'mechanic-browser', bridge.password)
    await comment(page, 'Исправлено после возврата')
    await waitForServerMessage(bridge, 'Исправлено после возврата')
    await page.reload()
    await submitReview(page, { clarification: 'Уточнение после возврата', camera: mobile, method: 'Отремонтировал' })
    await waitForServerMessage(bridge, 'Передано на проверку', 2)
    await drain(bridge)

    await switchUser(page, 'operator-browser', bridge.password)
    await page.getByRole('button', { name: 'Принять и закрыть', exact: true }).click()
    await expect(page.getByRole('heading', { name: /Проверить колесо робота/ })).not.toBeVisible()
    await drain(bridge)

    await openIssue(page)
    await openTaskConversation(page)
    const timeline = page.getByRole('region', { name: 'Чат задачи' }).locator('.task-message .issue-comment-text')
    const expected = ['Задача взята в работу', 'Заменено крепление колеса', 'Конец смены', 'Проверка второй сменой завершена', 'Проверено после передачи', 'Заменил мотор-колесо BD-01', 'Передано на проверку', 'Повторить проверку', 'Исправлено после возврата', 'Уточнение после возврата', 'Передано на проверку']
    await expect.poll(async () => {
      const texts = await timeline.allTextContents()
      let cursor = -1
      return expected.every(fragment => { cursor = texts.findIndex((text, index) => index > cursor && text.includes(fragment)); return cursor >= 0 })
    }).toBe(true)

    const evidence = await snapshot(bridge)
    expect(evidence.field_values[DEFECT_FIELD]).toBe('BD-01')
    expect(evidence.counts.upload).toBe(2)
    expect(evidence.field_values.components).toEqual(['wheels'])
    expect(evidence.field_values.solutionMethod).toBe('REPAIR')
    expect(evidence.counts.repair_fields).toBe(2)
    expect(evidence.counts['transition:start']).toBe(1)
    expect(evidence.counts['transition:review']).toBe(2)
    expect(evidence.counts['transition:return']).toBe(1)
    expect(evidence.counts['transition:close']).toBe(1)
    const commentActions = evidence.actions.filter(action => action.action === 'comment')
    const attachmentActions = evidence.actions.filter(action => action.action === 'attach')
    expect(commentActions).toHaveLength(8)
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
  } catch (error) {
    // Keep the original assertion; collect bounded evidence before teardown can
    // close the bridge or discard the queue. No extra synchronization on success.
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      const evidence = await Promise.race([
        Promise.allSettled([
          snapshot(bridge),
          page.evaluate(() => new Promise<unknown>((resolve, reject) => {
            const open = indexedDB.open('robopark-offline')
            open.onerror = () => reject(open.error)
            open.onsuccess = () => {
              const db = open.result
              if (!db.objectStoreNames.contains('actions')) { db.close(); resolve([]); return }
              const transaction = db.transaction('actions', 'readonly')
              const request = transaction.objectStore('actions').getAll()
              request.onsuccess = () => resolve(request.result)
              request.onerror = () => reject(request.error)
              transaction.oncomplete = transaction.onabort = () => db.close()
            }
          })),
        ]),
        new Promise<string>(resolve => { timer = setTimeout(() => resolve('diagnostics timed out'), 2000) }),
      ])
      await test.info().attach('task-lifecycle-sync.json', {
        body: JSON.stringify({ actor: session.actor, syncExchanges, evidence }, null, 2),
        contentType: 'application/json',
      })
    } catch { /* Diagnostic collection must not replace the original failure. */ }
    finally { clearTimeout(timer) }
    throw error
  } finally {
    tearingDown = true
    await page.context().setOffline(true).catch(() => {})
    if (!page.isClosed()) await page.close({ runBeforeUnload: false })
    await new Promise(resolve => setTimeout(resolve, 250))
    await http.close()
    await bridge.close()
  }
}

test.use({ trace: 'retain-on-failure' })

test.beforeEach(() => { process.env.DIAGNOSTIC_E2E_MUTATION = 'task-lifecycle' })
test.afterEach(() => { delete process.env.DIAGNOSTIC_E2E_MUTATION })

test('desktop completes the full lifecycle through visible controls exactly once', async ({ page }) => {
  test.setTimeout(60_000)
  await runLifecycle(page, 1440)
})

test('390x844 completes the full lifecycle, outage recovery and mobile contract', async ({ page }) => {
  test.setTimeout(60_000)
  await runLifecycle(page, 390)
})
