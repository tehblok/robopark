import { createHash } from 'node:crypto'
import { expect, test } from '@playwright/test'
import { startHttpFixture } from '../support/httpFixture'
import { FIXED_TIME, installOperational, issue, snapshot } from './fixtures'

const repair = { ...issue, claim: { park_id: 7 }, workflow: {
  owner: { login: 'mechanic-e2e', display: 'Механик смены' }, review_state: null,
  display_status: 'in_progress' as const, sync_state: 'synced' as const, has_current_cycle_comment: true,
} }
const photo = { name: 'repair.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jA/0AAAAASUVORK5CYII=', 'base64') }

async function expectTaskGeometry(page: import('@playwright/test').Page) {
  const header = await page.locator('.rp-work-detail-pane [data-task-header]').boundingBox()
  const body = await page.locator('.rp-work-detail-pane [data-task-body]').boundingBox()
  const workflow = await page.locator('.rp-work-sections > .rp-tabs').boundingBox()
  expect(header && body && workflow).toBeTruthy()
  expect(Math.abs(header!.x - body!.x)).toBeLessThanOrEqual(1)
  expect(Math.abs(header!.x + header!.width - body!.x - body!.width)).toBeLessThanOrEqual(1)
  expect(workflow!.x).toBeGreaterThanOrEqual(header!.x - 1)
  expect(workflow!.x + workflow!.width).toBeLessThanOrEqual(header!.x + header!.width + 1)
  await expect(page.getByRole('tablist', { name: 'Разделы задачи' }).getByRole('tab')).toHaveCount(4)
}

for (const width of [390, 1440]) {
  test(`Classic repair and robot check preserve draft and photo ${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    let snapshotRequests = 0
    page.on('request', request => { if (new URL(request.url()).pathname.includes('/snapshot')) snapshotRequests++ })
    await installOperational(page, { issue: repair, routes: [
      { method: 'GET', path: '/api/tracker/issues/ROBOPARK-42/timeline', handler: () => ({ json: [{ id: 'm1', kind: 'tracker', author: 'Оператор', text: 'Проверить колесо перед выдачей', created_at: FIXED_TIME, sync_state: 'synced', attachments: [] }] }) },
    ] })
    await page.goto('/work/ROBOPARK-42?park=7')
    await expect(page.getByRole('tab', { name: 'Задача', exact: true })).toBeVisible()
    await expectTaskGeometry(page)
    expect(snapshotRequests).toBe(0)
    await page.getByRole('button', { name: 'История и сообщения' }).click()
    const comment = page.getByRole('textbox', { name: 'Комментарии', exact: true })
    await comment.fill('Колесо заменено, крепление проверено')
    await page.getByLabel('Выбрать фото', { exact: true }).setInputFiles(photo)
    await expect(page.getByText('Проверить колесо перед выдачей', { exact: true })).toBeVisible()
    await expect(comment).toHaveValue('Колесо заменено, крепление проверено')
    expect(await page.getByLabel('Выбрать фото', { exact: true }).evaluate((input: HTMLInputElement) => input.files?.[0]?.name)).toBe('repair.png')
    await page.getByRole('tab', { name: 'Проверка', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Состояние робота' })).toBeVisible()
    const loaded = snapshotRequests
    expect(loaded).toBeGreaterThan(0)
    await page.getByRole('tab', { name: 'Задача', exact: true }).click()
    await expect(comment).toHaveValue('Колесо заменено, крепление проверено')
    expect(snapshotRequests).toBe(loaded)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('repair-classic.png'), fullPage: true })
  })
}

test('Classic robot check uses one snapshot owner', async ({ page }) => {
  let snapshots = 0
  page.on('request', request => { if (new URL(request.url()).pathname.includes('/snapshot')) snapshots++ })
  await installOperational(page, { role: 'royal' })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await expect(page.locator('.rp-check-summary')).toBeVisible()
  const loaded = snapshots
  await expect(page.locator('.classic-robot-layout')).toBeVisible()
  expect(snapshots).toBe(loaded)
})

test('Classic approval locks synchronously, reports 503 and recovers without an unhandled rejection', async ({ page }) => {
  const workflow = { owner: { display: 'Механик смены', login: 'mechanic-e2e' }, review_state: 'pending' as const,
    display_status: 'review' as const, sync_state: 'saved' as const, has_current_cycle_comment: true }
  let approvals = 0
  const pageErrors: Error[] = []
  page.on('pageerror', error => pageErrors.push(error))
  await installOperational(page, { role: 'operator', issue: { ...issue, workflow }, routes: [
    { method: 'POST', path: '/api/tracker/issues/ROBOPARK-42/review/approve', handler: () => {
      approvals++
      if (approvals === 1) return { status: 503, json: { detail: 'unavailable' } }
      return { json: { key: issue.key, action: 'approve-review', status: 'Закрыт', actor: 'operator.test', performed_at: FIXED_TIME, sync_state: 'saved', workflow: { ...workflow, review_state: 'closed', display_status: 'closed' } } }
    } },
  ] })
  await page.goto('/work/ROBOPARK-42?park=7')
  const action = page.getByRole('button', { name: 'Принять и закрыть' })
  await action.evaluate(button => { button.click(); button.click() })
  await expect.poll(() => approvals).toBe(1)
  await expect(page.getByRole('alert')).toBeVisible()
  expect(pageErrors).toEqual([])
  await action.click()
  await expect.poll(() => approvals).toBe(2)
  await expect(page).toHaveURL(/\/work\?park=7/)
})

test('Classic parts disclosure reveals and hides its existing content', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { issue: repair })
  await page.goto('/work/ROBOPARK-42?park=7')
  const disclosure = page.getByRole('button', { name: 'Списать запчасть' })
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
  await disclosure.click()
  await expect(disclosure).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('#parts')).toBeVisible()
  await disclosure.click()
  await expect(disclosure).toHaveAttribute('aria-expanded', 'false')
})

test('denied robot check does not reveal readings in Classic', async ({ page }) => {
  await installOperational(page, { role: 'royal', routes: [
    { method: 'GET', path: /^\/api\/emergency\/[^/]+\/snapshot$/, handler: () => ({ status: 403, json: { detail: 'forbidden' } }) },
  ] })
  await page.goto(`/robots/${snapshot.vin}/check?park=7&tab=scheme`)
  await expect(page.locator('.rp-check-summary')).toHaveCount(0)
})

test('review keeps defect code and one photo through an upload failure and reload', async ({ page }) => {
  let unavailable = true
  type Upload = { media_id: string; dependent_action_id: string; issue_key: string; device_id: string; mime_type: string; name: string; sha256: string; size_bytes: number }
  type Review = { client_action_id: string; action: string; payload: { defect_code?: string; media_id?: string } }
  const starts: Upload[] = []
  const chunks: Buffer[] = []
  const reviews: Review[] = []
  const lifecycle: string[] = []
  let completions = 0
  const media = await startHttpFixture(async request => {
    const path = new URL(request.url).pathname
    if (request.method === 'POST' && path === '/api/media/uploads') {
      const input = await request.json() as Upload
      expect(input).toMatchObject({ issue_key: 'ROBOPARK-42', name: 'repair.png' })
      expect(input.device_id).toBeTruthy()
      expect(input.dependent_action_id).toBeTruthy()
      expect(input.mime_type).toMatch(/^image\/(png|webp)$/)
      if (starts.length) expect(input).toEqual(starts[0])
      starts.push(input)
      if (unavailable) return Response.json({ detail: 'offline' }, { status: 503 })
      return Response.json({ upload_id: 'review-photo', received_offset: 0, completed: false, status: 'active' })
    }
    if (request.method === 'PUT' && path === '/api/media/uploads/review-photo/chunks/0') {
      const chunk = Buffer.from(await request.arrayBuffer())
      chunks.push(chunk)
      expect(createHash('sha256').update(chunk).digest('hex')).toBe(request.headers.get('X-Chunk-SHA256'))
      return Response.json({ received_offset: chunk.length })
    }
    if (request.method === 'POST' && path === '/api/media/uploads/review-photo/complete') {
      completions++
      lifecycle.push('complete')
      return Response.json({ upload_id: 'review-photo', media_id: starts[0].media_id, completed: true })
    }
    return Response.json({ detail: 'unexpected_media_request' }, { status: 404 })
  })
  try {
    await page.setViewportSize({ width: 390, height: 900 })
    await installOperational(page, { issue: repair, routes: [
      { method: 'GET', path: '/api/tracker/defect-codes', handler: () => ({ json: [{ code: 'BD-01', label: 'Вмятина', description: null }] }) },
      { method: 'POST', path: '/api/sync/batch', handler: async request => {
        const batch = await request.json() as { actions: Review[] }
        const submittedReviews = batch.actions.filter(action => action.action === 'submit_review')
        if (submittedReviews.length) lifecycle.push('submit_review')
        reviews.push(...submittedReviews)
        return { json: {
          results: batch.actions.map(action => ({ client_action_id: action.client_action_id, state: 'confirmed', code: null, result: {} })),
          deltas: {}, revisions: {}, revoked_scopes: [],
        } }
      } },
    ] })
    await page.route('**/api/media/uploads**', route => route.continue({ url: `${media.origin}${new URL(route.request().url()).pathname}` }))
    await page.goto('/work/ROBOPARK-42?park=7')
    await page.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
    const form = page.locator('form').filter({ has: page.getByLabel('Код дефекта') })
    await form.getByLabel('Код дефекта').fill('BD-01')
    await form.getByLabel('Выбрать файл').setInputFiles(photo)
    await expect(form.getByRole('img', { name: 'Предпросмотр repair.png' })).toBeVisible()
    await form.getByRole('button', { name: 'Передать на проверку', exact: true }).click()
    await expect(form).toHaveCount(0)
    await expect.poll(() => starts.length).toBeGreaterThan(0)
    const queue = page.getByRole('region', { name: 'Центр синхронизации' })
    await page.getByRole('button', { name: /^Открыть центр синхронизации/ }).click()
    await expect(queue.getByRole('listitem')).toHaveCount(2)
    await expect(queue.getByRole('listitem').filter({ hasText: /^Передать на проверку · ROBOPARK-42/ })).toBeVisible()
    await expect(queue.getByRole('listitem').filter({ hasText: /^Фото · ROBOPARK-42/ })).toBeVisible()
    expect(reviews).toHaveLength(0)
    expect(chunks).toHaveLength(0)
    await page.reload()
    await page.getByRole('button', { name: /^Открыть центр синхронизации/ }).click()
    await expect(queue.getByRole('listitem')).toHaveCount(2)
    expect(reviews).toHaveLength(0)
    unavailable = false
    await page.reload()
    await expect.poll(() => reviews.length).toBe(1)
    expect(reviews[0].payload).toMatchObject({ defect_code: 'BD-01', media_id: starts[0].media_id })
    expect(reviews[0].client_action_id).toBe(starts[0].dependent_action_id)
    expect(new Set(starts.map(input => input.media_id)).size).toBe(1)
    expect(starts.every(input => input.name === 'repair.png')).toBe(true)
    expect(chunks).toHaveLength(1)
    expect(chunks[0].length).toBe(starts[0].size_bytes)
    expect(createHash('sha256').update(chunks[0]).digest('hex')).toBe(starts[0].sha256)
    expect(completions).toBe(1)
    expect(lifecycle).toEqual(['complete', 'submit_review'])
    await page.getByRole('button', { name: /^Открыть центр синхронизации/ }).click()
    await expect(queue.getByRole('listitem')).toHaveCount(0)
  } finally {
    await page.goto('about:blank')
    await media.close()
  }
})
