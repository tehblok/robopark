import { expect, test, type Page } from '@playwright/test'
import type { Paged, TrackerActionResult, TrackerComment, TrackerIssueDetail } from '../../src/api'
import type { MockRoute } from '../support/mockApi'
import { FIXED_TIME, installOperational, issue, settlePage, snapshot } from './fixtures'

const rootKey = 'ROBOPARK-42'
const firstRepair: TrackerIssueDetail = {
  ...issue, key: 'ROBOPARK-200', summary: 'Проверить крепление батареи', type: 'repair', priority: 'minor',
  created_at: '2026-08-20T09:00:00Z', url: 'https://tracker.example.invalid/ROBOPARK-200',
}
const secondRepair: TrackerIssueDetail = {
  ...issue, key: 'ROBOPARK-201', summary: 'Заменить повреждённый кожух', type: 'repair', priority: 'critical',
  created_at: '2026-08-21T09:00:00Z', url: 'https://tracker.example.invalid/ROBOPARK-201',
}
const closedRepairs: TrackerIssueDetail[] = Array.from({ length: 11 }, (_, index) => ({
  ...issue, key: `ROBOPARK-${300 + index}`, summary: `Выполненный ремонт ${index + 1}`,
  type: 'repair', priority: index % 2 === 0 ? 'minor' : 'critical',
  status: 'Закрыт', status_key: 'closed', resolution: 'fixed',
  created_at: `2026-08-${10 + index}T09:00:00Z`, url: `https://tracker.example.invalid/ROBOPARK-${300 + index}`,
}))

async function installWorkTabs(page: Page) {
  const queries: URLSearchParams[] = []
  const emergency: string[] = []
  const comments = new Map<string, TrackerComment[]>()
  const mutations: { key: string; text: string }[] = []
  const issues = new Map([issue, firstRepair, secondRepair, ...closedRepairs].map(value => [value.key, value]))
  page.on('request', request => {
    const url = new URL(request.url())
    if (url.pathname.startsWith('/api/emergency/')) emergency.push(url.pathname)
  })
  const routes: MockRoute[] = [
    { method: 'GET', path: '/api/tracker/issues', handler: request => {
      const params = new URL(request.url).searchParams
      const offset = Number(params.get('offset') ?? 0)
      const limit = Number(params.get('limit') ?? 50)
      if (!params.has('robot_exact')) {
        return { json: { items: [issue], total: 101, offset, limit, has_more: true } satisfies Paged<TrackerIssueDetail> }
      }
      queries.push(params)
      const source = params.get('status') === 'closed' ? closedRepairs : [firstRepair, secondRepair]
      const available = source.filter(value => value.key !== params.get('exclude_key'))
      return { json: {
        items: available.slice(offset, offset + limit), total: available.length,
        offset, limit, has_more: offset + limit < available.length,
      } satisfies Paged<TrackerIssueDetail> }
    } },
    { method: 'GET', path: /^\/api\/tracker\/issues\/ROBOPARK-\d+$/, handler: request => {
      const key = new URL(request.url).pathname.split('/').at(-1)!
      const detail = issues.get(key)
      return detail ? { json: detail } : { status: 404, json: { detail: 'not_found' } }
    } },
    { method: 'GET', path: /^\/api\/tracker\/issues\/ROBOPARK-\d+\/comments$/, handler: request => {
      const key = new URL(request.url).pathname.split('/').at(-2)!
      return { json: comments.get(key) ?? [] }
    } },
    { method: 'GET', path: /^\/api\/tracker\/transitions\/ROBOPARK-\d+$/, handler: () => ({ json: [] }) },
    { method: 'POST', path: /^\/api\/tracker\/issues\/ROBOPARK-\d+\/comment$/, handler: async request => {
      const key = new URL(request.url).pathname.split('/').at(-2)!
      const { text }: { text: string } = await request.json()
      mutations.push({ key, text })
      comments.set(key, [...(comments.get(key) ?? []), {
        id: `comment-${mutations.length}`, text, author: 'Механик смены',
        author_login: 'mechanic.test', created_at: FIXED_TIME, attachments: [],
      }])
      return { json: {
        key, action: 'comment', status: 'ok', actor: 'mechanic.test', performed_at: FIXED_TIME,
      } satisfies TrackerActionResult }
    } },
  ]
  await installOperational(page, { routes })
  return { queries, emergency, mutations }
}

async function expectWorkLocation(page: Page, key: string, view?: string) {
  await expect.poll(() => ({
    pathname: new URL(page.url()).pathname,
    ...Object.fromEntries(new URL(page.url()).searchParams),
  })).toMatchObject({
    pathname: `/work/${key}`, park: '7', status: 'queued', page: '2',
    ...(view ? { view } : {}),
  })
  if (!view) expect(new URL(page.url()).searchParams.has('view')).toBe(false)
}

function expectRelatedQuery(params: URLSearchParams, key: string, closed: boolean, offset = '0') {
  expect(Object.fromEntries(params)).toMatchObject({
    queue: 'ROBOPARK', park: 'north', robot_exact: '447', related_repairs: 'true',
    exclude_key: key, open_only: closed ? 'false' : 'true', sort: 'oldest', limit: '10', offset,
    ...(closed ? { status: 'closed' } : {}),
  })
  if (!closed) expect(params.has('status')).toBe(false)
  expect(params.has('priority')).toBe(false)
}

for (const width of [390, 1440]) {
  test(`work tabs preserve a draft, nested blocker navigation and embedded check at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { queries, emergency, mutations } = await installWorkTabs(page)
    await page.goto(`/work/${rootKey}?park=7&status=queued&page=2`)
    await expect(page.getByRole('heading', { name: `Задача ${rootKey}`, exact: true })).toBeVisible()
    for (const name of ['Задача', 'Открытые задачи', 'Закрытые задачи', 'Проверка робота']) {
      await expect(page.getByRole('tab', { name, exact: true })).toBeVisible()
    }
    await expect(page.getByRole('tab', { name: 'Задача', exact: true })).toHaveAttribute('aria-selected', 'true')
    await settlePage(page)
    expect(queries).toEqual([])
    expect(emergency).toEqual([])

    const draft = 'Черновик комментария главного блокера'
    await page.getByRole('textbox', { name: 'Комментарии', exact: true }).fill(draft)
    await page.getByRole('tab', { name: 'Открытые задачи', exact: true }).click()
    await expectWorkLocation(page, rootKey, 'open')
    const openPanel = page.getByRole('tabpanel')
    await expect(openPanel.getByRole('heading', { name: 'Открытые задачи робота 447', exact: true })).toBeVisible()
    await expect(openPanel.getByRole('button', { name: /^Открыть задачу ROBOPARK-/ })).toHaveCount(2)
    await expect.poll(() => queries.length).toBeGreaterThan(0)
    expectRelatedQuery(queries.at(-1)!, rootKey, false)
    expect(queries.every(params => params.get('status') !== 'closed')).toBe(true)
    expect(emergency).toEqual([])
    await page.getByRole('tab', { name: 'Задача', exact: true }).click()
    await expectWorkLocation(page, rootKey)
    await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toHaveValue(draft)
    expect(mutations).toEqual([])

    await page.getByRole('tab', { name: 'Открытые задачи', exact: true }).click()
    await page.getByRole('tabpanel').getByRole('button', { name: `Открыть задачу ${firstRepair.key}: ${firstRepair.summary}`, exact: true }).click()
    await expectWorkLocation(page, firstRepair.key)
    expect(new URL(page.url()).searchParams.get('blocker')).toBe(rootKey)
    await expect(page.getByRole('heading', { name: `Задача ${firstRepair.key}`, exact: true })).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Комментарии', exact: true })).toHaveValue('')
    const comment = 'Крепление батареи проверено в дополнительной задаче'
    await page.getByRole('textbox', { name: 'Комментарии', exact: true }).fill(comment)
    await page.getByRole('button', { name: 'Отправить', exact: true }).click()
    await expect(page.getByText(comment, { exact: true })).toBeVisible()
    expect(mutations).toEqual([{ key: firstRepair.key, text: comment }])

    await page.getByRole('tab', { name: 'Открытые задачи', exact: true }).click()
    await expectWorkLocation(page, firstRepair.key, 'open')
    await expect.poll(() => queries.at(-1)?.get('exclude_key')).toBe(firstRepair.key)
    expectRelatedQuery(queries.at(-1)!, firstRepair.key, false)
    await page.getByRole('tabpanel').getByRole('button', { name: `Открыть задачу ${secondRepair.key}: ${secondRepair.summary}`, exact: true }).click()
    await expectWorkLocation(page, secondRepair.key)
    expect(new URL(page.url()).searchParams.get('blocker')).toBe(rootKey)
    await page.getByRole('tab', { name: 'Проверка робота', exact: true }).click()
    await expectWorkLocation(page, secondRepair.key, 'check')
    await page.getByRole('tab', { name: 'Схема', exact: true }).click()
    await expect.poll(() => new URL(page.url()).searchParams.get('check_tab')).toBe('scheme')
    await expect(page.getByRole('tab', { name: 'Схема', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.rp-check-photo-frame img')).toBeVisible()
    const summaryBox = await page.locator('.rp-check-first-level').boundingBox()
    const detailBox = await page.locator('.rp-check-detail').boundingBox()
    const panelBox = await page.locator('#work-panel-check').boundingBox()
    expect(detailBox!.y).toBeGreaterThanOrEqual(summaryBox!.y + summaryBox!.height)
    expect(detailBox!.width).toBeGreaterThan(panelBox!.width * .9)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await expect.poll(() => emergency).toEqual(expect.arrayContaining([
      '/api/emergency/resolve', `/api/emergency/${snapshot.vin}/snapshot`,
    ]))
    const checkUrl = page.url()
    await page.reload()
    await expect(page).toHaveURL(checkUrl)
    await expect(page.getByRole('tab', { name: 'Проверка робота', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('tab', { name: 'Схема', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.rp-check-photo-frame img')).toBeVisible()
    await page.getByRole('link', { name: `К главному блокеру ${rootKey}`, exact: true }).click()
    await expectWorkLocation(page, rootKey)
    expect(new URL(page.url()).searchParams.has('check_tab')).toBe(false)
    await expect(page.getByRole('tab', { name: 'Задача', exact: true })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('heading', { name: `Задача ${rootKey}`, exact: true })).toBeVisible()
  })

  test(`closed repair tab loads any priority lazily and paginates ten oldest first at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const { queries, emergency } = await installWorkTabs(page)
    await page.goto(`/work/${rootKey}?park=7&status=queued&page=2`)
    await expect(page.getByRole('heading', { name: `Задача ${rootKey}`, exact: true })).toBeVisible()
    await settlePage(page)
    expect(queries).toEqual([])
    await page.getByRole('tab', { name: 'Закрытые задачи', exact: true }).click()
    await expectWorkLocation(page, rootKey, 'closed')
    const panel = page.getByRole('tabpanel')
    await expect(panel.getByRole('heading', { name: 'Закрытые задачи робота 447', exact: true })).toBeVisible()
    const rows = panel.getByRole('button', { name: /^Открыть задачу ROBOPARK-/ })
    await expect(rows).toHaveCount(10)
    await expect(rows.first()).toHaveAttribute('aria-label', 'Открыть задачу ROBOPARK-300: Выполненный ремонт 1')
    await expect(rows.nth(1)).toHaveAttribute('aria-label', 'Открыть задачу ROBOPARK-301: Выполненный ремонт 2')
    await expect(rows.last()).toHaveAttribute('aria-label', 'Открыть задачу ROBOPARK-309: Выполненный ремонт 10')
    expectRelatedQuery(queries.at(-1)!, rootKey, true)
    expect(queries.every(params => params.get('status') === 'closed')).toBe(true)
    expect(emergency).toEqual([])
    await expect(panel.getByRole('button', { name: 'Назад', exact: true })).toBeDisabled()
    await panel.getByRole('button', { name: 'Вперёд', exact: true }).click()
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toHaveAttribute('aria-label', 'Открыть задачу ROBOPARK-310: Выполненный ремонт 11')
    expectRelatedQuery(queries.at(-1)!, rootKey, true, '10')
    await expect(panel.getByRole('button', { name: 'Вперёд', exact: true })).toBeDisabled()
    await expectWorkLocation(page, rootKey, 'closed')
    await panel.getByRole('button', { name: 'Назад', exact: true }).click()
    await expect(rows).toHaveCount(10)
    await expect(rows.first()).toHaveAttribute('aria-label', 'Открыть задачу ROBOPARK-300: Выполненный ремонт 1')
    await expectWorkLocation(page, rootKey, 'closed')
  })
}
