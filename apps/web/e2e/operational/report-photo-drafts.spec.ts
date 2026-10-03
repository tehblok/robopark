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
