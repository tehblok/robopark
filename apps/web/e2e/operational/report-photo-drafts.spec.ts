import { expect, test } from '../support/persistentWebKit'
import { startHttpFixture } from '../support/httpFixture'
import { installOperational } from './fixtures'

test('photo draft survives browser reload and resumes an incomplete attachment on the same report', async ({ page }) => {
  let creates = 0
  let uploads = 0
  const uploaded: string[] = []
  const attachments = await startHttpFixture(async request => {
    uploads += 1
    uploaded.push(await request.text())
    if (uploads === 1) return Response.json({ detail: 'offline' }, { status: 503 })
    return Response.json({ id: 11, kind: 'device_photo', filename: 'robot.jpg', content_type: 'image/jpeg', size_bytes: 11 })
  })
  try {
    await installOperational(page, { role: 'mechanic', routes: [
      { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [] }) },
      { method: 'POST', path: '/api/reports', handler: () => {
        creates += 1
        return { json: { id: 42, kind: 'mechanic_problem', status: 'open', park_id: 7, author_user_id: 100, target_role: 'operator', title: 'С фото', body: '', tracker_key: null, tracker_url: null, created_at: '2026-09-06T00:00:00Z', updated_at: '2026-09-06T00:00:00Z', resolved_at: null, parent_report_id: null, return_comment: null } }
      } },
    ] })
    await page.route('**/api/reports/42/attachments', route => route.continue({ url: `${attachments.origin}/api/reports/42/attachments` }))
    await page.goto('/reports/new?park=7')
    await page.getByRole('button', { name: 'Проблема', exact: true }).click()
    await page.getByRole('textbox', { name: 'Заголовок *' }).fill('С фото')
    await page.getByLabel('Файл', { exact: true }).setInputFiles({ name: 'robot.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('photo bytes') })
    await expect(page.getByText('Черновик сохранён на этом устройстве.', { exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByText(/Выбран файл: robot.jpg/)).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Заголовок *' })).toHaveValue('С фото')
    await page.getByRole('button', { name: 'Создать', exact: true }).click()
    await expect(page.getByText('Репорт отправлен оператору.', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Прикрепить файл', exact: true }).click()
    await expect.poll(() => uploads).toBe(1)
    await expect(page.getByRole('alert').filter({ hasText: 'Не удалось выполнить действие' })).toBeVisible()
    await expect(page.getByText('Черновик сохранён на этом устройстве.', { exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByText(/Выбран файл: robot.jpg/)).toBeVisible()
    await expect(page.getByText(/Репорт №42/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Создать', exact: true })).toBeDisabled()
    await page.getByRole('button', { name: 'Прикрепить файл', exact: true }).click()
    await expect(page.getByText('Файл прикреплён к созданному репорту.', { exact: true })).toBeVisible()
    expect(creates).toBe(1)
    expect(uploads).toBe(2)
    expect(uploaded).toHaveLength(2)
    expect(uploaded.every(body => body.includes('photo bytes'))).toBe(true)
    await page.getByRole('button', { name: 'Удалить черновик', exact: true }).click()
    await page.reload()
    await expect(page.getByText(/Выбран файл:/)).toHaveCount(0)
    await expect(page.getByText(/Репорт №42/)).toHaveCount(0)
  } finally {
    await attachments.close()
  }
})

for (const delayed of [false, true]) test(`offline media bytes survive ${delayed ? 'delayed' : 'immediate'} state updates and a page reload`, async ({ page }) => {
  await page.route('**/__offline_media_integrity__', route => route.fulfill({
    contentType: 'text/html', body: '<!doctype html><title>Offline media integrity</title>',
  }))
  await page.goto('/__offline_media_integrity__')
  const result = await page.evaluate(async () => {
    const moduleUrl = '/src/pwa/offlineDb.ts'
    const { openOfflineDb } = await import(moduleUrl) as typeof import('../../src/pwa/offlineDb')
    const scope = { account: 'media-probe', role: 'mechanic', permissions: 'tracker.read', park: '7', schema: 1 }
    const db = await openOfflineDb(scope)
    const contents = 'preserved-photo-bytes'
    await db.putMedia({
      id: 'photo', actionId: 'upload-photo', issueKey: 'ROBOPARK-42', name: 'photo.txt',
      blob: new Blob([contents], { type: 'text/plain' }), mimeType: 'text/plain',
      sha256: 'a'.repeat(64), sizeBytes: contents.length, state: 'ready', createdAt: 1, updatedAt: 1,
    })
    db.close()
    return contents
  })
  await page.reload()
  await page.evaluate(async (delayed) => {
    const moduleUrl = '/src/pwa/offlineDb.ts'
    const { openOfflineDb } = await import(moduleUrl) as typeof import('../../src/pwa/offlineDb')
    const db = await openOfflineDb({ account: 'media-probe', role: 'mechanic', permissions: 'tracker.read', park: '7', schema: 1 })
    await db.claimLease('media-probe', Date.now(), 60_000)
    for (let i = 0; i < (delayed ? 2 : 20); i += 1) {
      const before = await db.getMedia('photo')
      if (!before) throw new Error(`missing media before update ${i}`)
      // WebKit tracks file-backed Blob mtimes in whole seconds. Exercise a
      // persisted read held across that boundary before rewriting metadata.
      if (delayed) await new Promise(resolve => setTimeout(resolve, 1100))
      const committed = await db.transactionIfLease('media-probe', () => Date.now(), writer => {
        writer.putMedia({ ...before, state: i % 2 ? 'ready' : 'uploading', updatedAt: i + 2 })
      })
      if (!committed) throw new Error('media probe lost its lease')
    }
    const after = await db.getMedia('photo')
    if (!after || after.updatedAt !== (delayed ? 3 : 21) || await after.blob.text() !== 'preserved-photo-bytes') throw new Error('media bytes lost after metadata-only updates')
    db.close()
  }, delayed)
  await page.reload()
  const restored = await page.evaluate(async () => {
    const moduleUrl = '/src/pwa/offlineDb.ts'
    const { openOfflineDb } = await import(moduleUrl) as typeof import('../../src/pwa/offlineDb')
    const db = await openOfflineDb({ account: 'media-probe', role: 'mechanic', permissions: 'tracker.read', park: '7', schema: 1 })
    try {
      const media = await db.getMedia('photo')
      if (!media) throw new Error('media missing after reload')
      return await media.blob.text()
    } finally { db.close() }
  })
  expect(restored).toBe(result)
})
