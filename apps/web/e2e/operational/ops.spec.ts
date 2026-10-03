import { expect, test } from '@playwright/test'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import type { MockRoute } from '../support/mockApi'
import { installOperational, settlePage, userForRole } from './fixtures'

const kinds = [
  'ota-update', 'rollback', 'package-inspect', 'package-update', 'service-restart', 'reboot',
  'backup', 'backup-verify', 'backup-restore', 'cleanup-preview', 'cleanup-execute',
  'docker-image-preview', 'docker-image-execute', 'builder-cache-preview', 'builder-cache-execute',
  'diagnostics', 'usb-discover', 'usb-format', 'usb-select',
]
const summary = {
  sampled_at: '2026-09-30T12:00:00Z', metrics_stale: false, worker_health: 'worker_healthy',
  online: { total: 1, by_role: { royal: 1 }, by_park: {} }, tracker: { state: 'not_configured' },
  sync: { cursor_age_seconds: null, pending_action_count: 0, oldest_pending_action_age_seconds: null, retry_count: 0, needs_attention_count: 0, last_success_at: null, last_error: null, worker_lease_state: 'active' },
  push: { pending: 0, needs_attention: 0 }, metrics: { host: {} }, release: { version: '0.2.0' },
}
const capabilities = {
  state: 'ready', generated_at: '2026-09-30T12:00:00Z', expires_at: '2099-09-30T12:05:00Z', revision: 'a'.repeat(64),
  operations: Object.fromEntries(kinds.map(kind => [kind, { available: kind === 'diagnostics', unavailable_reason: kind === 'diagnostics' ? null : 'capability_unavailable' }])),
}
const operationContext = {
  generated_at: '2026-09-30T12:00:00Z', expires_at: '2099-09-30T12:05:00Z',
  rollback_release: null, selected_device_uuid: null, packages: [], services: [], devices: [], backups: [],
}

function systemRoutes(operationRoutes: MockRoute[] = []): MockRoute[] {
  return [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: { active_users: [], metrics: [] } }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/ops/operation-context', handler: () => ({ json: operationContext }) },
    ...operationRoutes,
  ]
}

for (const width of [320, 1440]) test(`owner removes an obsolete OTA upload without its local file at ${width}px`, async ({ page }, testInfo) => {
  let removed = false
  const deletions: string[] = []
  const old = { upload_id: 'dd8d7279-e36d-45b4-980d-d07f20c3e360', filename: 'previous.ota', size: 2048,
    sha256: 'a'.repeat(64), offset: 2048, expires_at: 4_000_000_000, state: 'verified', chunk_size: 4 * 1024 ** 2,
    version: '0.2.0-rc.16', compatible_from: ['0.2.0-rc.15'] }
  await page.setViewportSize({ width, height: 900 })
  await installOperational(page, { user: userForRole('royal'), routes: systemRoutes([
    { method: 'GET', path: '/api/admin/ops/operations', handler: () => ({ json: { items: [] } }) },
    { method: 'GET', path: '/api/admin/ops/ota/uploads', handler: () => ({ json: { items: removed ? [] : [old] } }) },
    { method: 'DELETE', path: `/api/admin/ops/ota/uploads/${old.upload_id}`, handler: request => {
      deletions.push(new URL(request.url).pathname); removed = true
      return { status: 204 }
    } },
  ]) })
  await page.goto('/system?park=7')
  await page.getByRole('button', { name: 'Показать мои загрузки' }).click()
  const manager = page.getByRole('region', { name: 'Мои загрузки OTA' })
  await expect(manager).toContainText(old.filename)
  await expect(manager).toContainText(old.sha256)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await settlePage(page)
  await assertNoSeriousA11yViolations(page)
  await manager.screenshot({ path: testInfo.outputPath('ota-upload-manager.png') })
  await manager.getByRole('button', { name: /Удалить previous.ota/ }).click()
  await expect(manager).toContainText('Нет загрузок, доступных для удаления.')
  expect(deletions).toEqual([`/api/admin/ops/ota/uploads/${old.upload_id}`])
  await expect(page.getByRole('button', { name: /Установить 0/ })).toHaveCount(0)
})

for (const width of [320, 1440]) for (const theme of ['light', 'dark']) {
  test(`Royal system health Classic ${width} ${theme}: accessible responsive read-only panel`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    await installOperational(page, { user: userForRole('royal'), routes: [
      { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
      { method: 'GET', path: '/api/admin/settings/integrations', handler: () => ({ json: {} }) },
      { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: {} }) },
      { method: 'GET', path: '/api/admin/ops/job', handler: () => ({ json: { id: '', state: 'idle' } }) },
      { method: 'GET', path: '/api/admin/ops/release-status', handler: () => ({ json: {
        version: '1.2.0', build_id: 'a'.repeat(20), git_sha: 'a'.repeat(40), channel: 'stable', support_class: 'standard',
        released_at: '2026-09-01T00:00:00Z', supported_until: '2027-03-01T00:00:00Z', support_status: 'supported',
        operations_blocked: false, database_head: '0054_park_coordinates', installer_version: '1.2.0', available_update: null, bridges: [], cleanup: null,
      } }) },
      { method: 'GET', path: '/api/admin/ops/system-health', handler: () => ({ json: {
        version: '1.2.0', git_sha: 'a'.repeat(40), generated_at: new Date().toISOString(), overall: 'degraded',
        checks: [{ code: 'tuna_inactive', status: 'failed', message: 'Сервис Tuna', repair: 'restart_tuna' }],
        update: { state: 'rolled_back', publication: 'degraded' }, last_backup: { status: 'success', completed_at: new Date().toISOString() },
      } }) },
    ] })
    await page.goto('/admin/settings?park=7&tab=ops')
    await expect(page.getByRole('heading', { name: 'Здоровье системы' })).toBeVisible()
    await expect(page.getByText('Сервис Tuna', { exact: true })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Открыть системные операции' })).toHaveAttribute('href', '/system?park=7#system-operations')
    await expect(page.getByRole('button', { name: 'Обновить из GitHub' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Скачать диагностику' })).toHaveCount(0)
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await assertNoSeriousA11yViolations(page)
    await page.screenshot({ path: testInfo.outputPath(`system-health-${width}-${theme}.png`), fullPage: true })
  })
}

test('real System confirmation keeps keyboard focus, exact confirmation and Escape behavior', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await installOperational(page, { user: userForRole('royal'), routes: systemRoutes([
    { method: 'GET', path: '/api/admin/ops/operations', handler: () => ({ json: { items: [] } }) },
  ]) })
  await page.goto('/system?park=7')

  const trigger = page.getByRole('button', { name: 'Собрать диагностику' })
  await trigger.focus()
  await page.keyboard.press('Enter')
  const dialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  const confirmation = dialog.getByLabel('Введите ЗАПУСТИТЬ DIAGNOSTICS')
  const submit = dialog.getByRole('button', { name: 'Запустить' })
  await expect(dialog).toBeVisible()
  await expect(confirmation).toBeFocused()
  await expect(submit).toBeDisabled()
  await confirmation.fill('ЗАПУСТИТЬ DIAGNOSTICS')
  await expect(submit).toBeDisabled()
  await dialog.getByLabel('Пароль').fill('secret')
  await dialog.getByLabel('Код TOTP или восстановления').fill('123456')
  await expect(submit).toBeEnabled()
  await settlePage(page)
  await assertNoSeriousA11yViolations(page)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test('accepted System operation survives a delayed refresh started before dispatch', async ({ page }) => {
  let releaseStaleRead!: () => void
  const staleRead = new Promise<void>(resolve => { releaseStaleRead = resolve })
  let operationReads = 0
  const runningJob = {
    id: 'diagnostics-race', kind: 'diagnostics', receipt_state: 'accepted', state: 'running', phase: 'awaiting_host',
    error: null, progress_percent: 25, artifact_ready: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), host_result: null,
  }
  await installOperational(page, { user: userForRole('royal'), routes: systemRoutes([
    { method: 'GET', path: '/api/admin/ops/operations', handler: async () => {
      operationReads++
      if (operationReads === 2) await staleRead
      return { json: { items: [] } }
    } },
    { method: 'POST', path: '/api/admin/privileged-auth/reauthorize', handler: () => ({ json: { token: 'reauth-token', expires_in: 120 } }) },
    { method: 'POST', path: '/api/admin/ops/operations', handler: async request => {
      const payload = await request.json() as { operation_id: string }
      return { json: { ...runningJob, id: payload.operation_id } }
    } },
  ]) })
  await page.goto('/system?park=7')
  const start = page.getByRole('button', { name: 'Собрать диагностику' })
  await expect(start).toBeEnabled()

  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await expect.poll(() => operationReads).toBe(2)
  await start.click()
  const dialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await dialog.getByLabel('Введите ЗАПУСТИТЬ DIAGNOSTICS').fill('ЗАПУСТИТЬ DIAGNOSTICS')
  await dialog.getByLabel('Пароль').fill('secret')
  await dialog.getByLabel('Код TOTP или восстановления').fill('123456')
  await dialog.getByRole('button', { name: 'Запустить' }).click()

  await expect(page.getByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '25')
  releaseStaleRead()
  await expect(page.getByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '25')
  await expect(start).toBeDisabled()
})
