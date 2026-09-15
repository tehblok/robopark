import { expect, test, type Page } from '@playwright/test'
import type { User } from '../../src/api'
import type { MockResponse, MockRoute } from '../support/mockApi'
import { startDiagnosticApi } from '../support/diagnosticApi'
import { installOperational, userForRole } from './fixtures'

type Actor = 'mechanic' | 'mechanic-next' | 'operator'
type Bridge = Awaited<ReturnType<typeof startDiagnosticApi>>

function browserUser(role: 'mechanic' | 'operator'): User {
  const user = userForRole(role)
  return { ...user, username: `${role}-browser`, tracker_login: `${role}-browser` }
}

async function bridgeCall(bridge: Bridge, input: Record<string, unknown>, actor: Actor = 'mechanic') {
  return (bridge.call as unknown as (input: Record<string, unknown>, actor: Actor) => Promise<MockResponse>)(input, actor)
}

function trackerRoutes(bridge: Bridge, actor: () => Actor): MockRoute[] {
  const handler = async (request: Request) => {
    const url = new URL(request.url)
    const body = ['GET', 'HEAD'].includes(request.method) ? undefined : Buffer.from(await request.arrayBuffer()).toString('base64')
    return bridgeCall(bridge, {
      method: request.method,
      path: url.pathname.replace(/^\/api/, '') + url.search,
      body_base64: body,
      headers: Object.fromEntries(request.headers),
    }, actor())
  }
  return (['GET', 'POST', 'PUT', 'PATCH', 'DELETE'] as const).map(method => ({
    method,
    path: /^\/api\/tracker(?:\/|$)/,
    handler,
  }))
}

async function installLifecycle(page: Page, bridge: Bridge, actor: () => Actor, role: 'mechanic' | 'operator' = 'mechanic') {
  await installOperational(page, { user: browserUser(role), routes: trackerRoutes(bridge, actor) })
}

async function snapshot(bridge: Bridge) {
  const response = await bridgeCall(bridge, { control: 'snapshot' })
  return response.json as {
    counts: Record<string, number>
    field_values: Record<string, string>
    actions: Array<{ id: string; action: string; state: string }>
    timeline: string[]
  }
}

async function drain(bridge: Bridge) {
  const response = await bridgeCall(bridge, { control: 'drain' })
  expect(response.status).toBe(200)
}

test.beforeEach(() => { process.env.DIAGNOSTIC_E2E_MUTATION = 'task-lifecycle' })
test.afterEach(() => { delete process.env.DIAGNOSTIC_E2E_MUTATION })

test('desktop completes claim, collaboration, review return, resubmit and approval exactly once', async ({ page }) => {
  test.setTimeout(90_000)
  const bridge = await startDiagnosticApi()
  let actor: Actor = 'mechanic'
  try {
    await installLifecycle(page, bridge, () => actor)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/work?park=7')

    const claim = page.getByRole('button', { name: 'Взять в работу', exact: true })
    await claim.dblclick()
    await expect(page.getByRole('heading', { name: /Проверить колесо робота/ })).toBeVisible()
    await expect(page.getByRole('status').filter({ hasText: 'Отправляется в Tracker' }).first()).toBeVisible()
    await drain(bridge)
    await page.reload()

    const composer = page.getByRole('textbox', { name: 'Комментарии', exact: true })
    await composer.fill('Заменено крепление колеса')
    await page.getByRole('button', { name: 'Отправить', exact: true }).focus()
    await page.keyboard.press('Enter')
    await expect(page.getByText('Заменено крепление колеса', { exact: true })).toBeVisible()

    const disclosures = page.getByRole('group', { name: 'Дополнительные разделы задачи' }).getByRole('button')
    await expect(disclosures).toHaveText(['Заказать запчасть', 'Передать смену'])
    await disclosures.nth(0).click()
    await expect(disclosures.nth(0)).toBeFocused()
    await disclosures.nth(0).click()
    await disclosures.nth(1).click()
    await page.getByLabel('Логин сменщика').fill('mechanic-next-browser')
    await page.getByLabel('Причина передачи').fill('Конец смены')
    await page.getByRole('button', { name: 'Передать смену', exact: true }).last().click()

    actor = 'mechanic-next'
    await bridgeCall(bridge, {
      method: 'POST', path: '/tracker/issues/ROBOPARK-42/handoff',
      headers: { 'content-type': 'application/json', 'Idempotency-Key': 'handoff-back-0001' },
      body: JSON.stringify({ assignee: 'mechanic-browser', reason: 'Проверка второй сменой завершена', done: '', remaining: '', obstacles: '' }),
    }, actor)
    actor = 'mechanic'
    await page.reload()

    await page.getByRole('textbox', { name: 'Комментарии', exact: true }).fill('Проверено после передачи')
    await page.getByRole('button', { name: 'Отправить', exact: true }).click()
    await page.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
    await expect(page.getByRole('textbox', { name: 'Добавить уточнение' })).toBeVisible()
    expect((await bridgeCall(bridge, { control: 'submit_review', actor, idempotency_key: 'submit-review-0001', defect_code: 'BD-01' }, actor)).status).toBe(200)
    await drain(bridge)

    actor = 'operator'
    await bridgeCall(bridge, {
      method: 'POST', path: '/tracker/issues/ROBOPARK-42/review/return',
      headers: { 'content-type': 'application/json', 'Idempotency-Key': 'return-review-0001' },
      body: JSON.stringify({ reason: 'Повторить проверку', assignee: 'mechanic-browser' }),
    }, actor)
    await drain(bridge)
    actor = 'mechanic'
    await page.reload()
    await page.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
    await expect(page.getByRole('textbox', { name: 'Комментарий о выполненной работе' })).toBeVisible()
    expect((await bridgeCall(bridge, { control: 'submit_review', actor, idempotency_key: 'submit-review-0002', defect_code: 'BD-01', comment: 'Уточнение после возврата' }, actor)).status).toBe(200)
    await drain(bridge)

    actor = 'operator'
    await bridgeCall(bridge, {
      method: 'POST', path: '/tracker/issues/ROBOPARK-42/review/approve',
      headers: { 'Idempotency-Key': 'approve-review-0001' },
    }, actor)
    await drain(bridge)

    actor = 'mechanic'
    await page.reload()
    const chat = page.getByRole('region', { name: 'Чат задачи' })
    await expect(chat).toContainText('Задача взята в работу')
    await expect(chat).toContainText('Заменено крепление колеса')
    await expect(chat).toContainText('Конец смены')
    await expect(chat).toContainText('Повторить проверку')
    await expect(chat).toContainText('Уточнение после возврата')

    const evidence = await snapshot(bridge)
    expect(evidence.field_values['60df26695151a36df681d67b--theDefectCode']).toBe('BD-01')
    expect(evidence.counts['transition:start']).toBe(1)
    expect(evidence.counts['transition:review']).toBe(2)
    expect(evidence.counts['transition:return']).toBe(1)
    expect(evidence.counts['transition:close']).toBe(1)
    for (const [operation, count] of Object.entries(evidence.counts).filter(([key]) => key.startsWith('comment:'))) {
      expect(count, operation).toBe(1)
    }
    expect(evidence.actions.every(action => action.state === 'succeeded')).toBe(true)
  } finally {
    await bridge.close()
  }
})

test('Tracker outage preserves local claim and recovers one transition after reload', async ({ page }) => {
  const bridge = await startDiagnosticApi()
  let actor: Actor = 'mechanic'
  try {
    await installLifecycle(page, bridge, () => actor)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/work?park=7')
    await expect(page.getByRole('button', { name: 'Взять в работу' })).toBeVisible()
    await bridgeCall(bridge, { method: 'GET', path: '/tracker/issues/ROBOPARK-42' }, 'operator')
    await bridgeCall(bridge, { control: 'tracker', available: false })
    await page.getByRole('button', { name: 'Взять в работу' }).dblclick()
    await expect(page.getByRole('status').filter({ hasText: 'Отправляется в Tracker' }).first()).toBeVisible()

    await drain(bridge)
    expect((await snapshot(bridge)).actions.some(action => action.state === 'retry_wait')).toBe(true)
    await bridgeCall(bridge, { control: 'tracker', available: true })
    await drain(bridge)
    await page.reload()
    await expect(page.getByRole('status').filter({ hasText: 'Сохранено' }).first()).toBeVisible()
    await expect(page.getByText('Задача взята в работу', { exact: false })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    const evidence = await snapshot(bridge)
    expect(evidence.counts['transition:start']).toBe(1)
    expect(evidence.timeline.filter(text => text.includes('Задача взята в работу'))).toHaveLength(1)
  } finally {
    await bridge.close()
  }
})
