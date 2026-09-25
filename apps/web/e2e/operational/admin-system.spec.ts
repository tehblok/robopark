import { expect, test } from '@playwright/test'
import { installOperational, userForRole } from './fixtures'
import type { MockRoute } from '../support/mockApi'

const revision = 'a'.repeat(64)
const summary = {
  sampled_at: '2026-09-25T09:00:00Z', metrics_stale: false,
  online: { total: 4, by_role: { mechanic: 3, admin: 1 }, by_park: { '7': 4 } },
  sync: { cursor_age_seconds: 12, pending_action_count: 2, oldest_pending_action_age_seconds: 20, retry_count: 1, needs_attention_count: 1, last_success_at: '2026-09-25T08:59:00Z', last_error: null, worker_lease_state: 'active' },
  push: { pending: 1, needs_attention: 0 },
  metrics: { host: { cpu: { state: 'ok', load_1m: .4, cores: 4 }, disk: { total_bytes: 1000, free_bytes: 400 }, memory: { total_bytes: 1000, available_bytes: 500 }, postgresql: { state: 'ok' }, container: { state: 'ok' }, tuna: { state: 'ok' }, internet: { state: 'ok' }, requests: {}, storage: { bytes_to_reclaim: 0, cleanup_failed: false }, backup: { overdue: false } } }, release: { version: '0.2.0-rc.6' },
}
const kinds = ['release-update', 'reinstall', 'rollback', 'package-inspect', 'package-update', 'service-restart', 'reboot', 'backup', 'backup-verify', 'backup-restore', 'cleanup-preview', 'cleanup-execute', 'diagnostics', 'usb-discover', 'usb-format', 'usb-select']
const safe = new Set(['package-inspect', 'backup-verify', 'cleanup-preview', 'diagnostics', 'usb-discover', 'usb-select'])
const capabilities = { state: 'ready', generated_at: '2026-09-25T09:00:00Z', expires_at: '2099-09-25T09:05:00Z', revision, operations: Object.fromEntries(kinds.map(kind => [kind, { available: safe.has(kind), unavailable_reason: safe.has(kind) ? null : 'capability_unavailable' }])) }
const history = { active_users: [{ date: '2026-09-24', users: 6 }], metrics: [] }
const idle = { id: '', kind: '', state: 'idle', phase: '', progress_percent: null, error: null }

test('admin reads the compact system console at 390px without host controls', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const routes: MockRoute[] = [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
  ]
  await installOperational(page, { user: userForRole('admin'), routes })
  await page.goto('/system?park=7')
  await expect(page.getByRole('heading', { name: 'Система', level: 1 })).toBeVisible()
  await expect(page.getByRole('img', { name: 'Активные пользователи за 7 дней' })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Активные пользователи за 7 дней — значения' })).toContainText('2026-09-24')
  await expect(page.getByText('Wi‑Fi')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Управляемые операции' })).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(/argv|командная строка/i)
})

test('royal confirms an available typed operation and resumes UUID progress at 1440px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  let acceptedId = ''
  const routes: MockRoute[] = [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/ops/job', handler: () => ({ json: acceptedId ? { id: acceptedId, kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 50, error: null } : idle }) },
    { method: 'POST', path: '/api/admin/privileged-auth/reauthorize', handler: async request => {
      const body = await request.json() as Record<string, string>
      expect(body.capability_revision).toBe(revision)
      expect(body.password).toBe('secret')
      expect(body.code).toBe('123456')
      acceptedId = body.operation_id
      return { json: { token: 'reauth', expires_in: 120 } }
    } },
    { method: 'POST', path: '/api/admin/ops/operations', handler: async request => {
      const body = await request.json() as Record<string, string>
      expect(request.headers.get('X-Privileged-Authorization')).toBe('reauth')
      expect(body).toEqual({ operation_id: acceptedId, kind: 'diagnostics', capability_revision: revision, confirmation: 'ЗАПУСТИТЬ DIAGNOSTICS' })
      return { json: { id: acceptedId, kind: 'diagnostics', state: 'running', phase: 'accepted', progress_percent: 0, error: null } }
    } },
  ]
  await installOperational(page, { user: userForRole('royal'), routes })
  await page.goto('/system?park=7')
  await page.getByRole('button', { name: 'Собрать диагностику' }).click()
  const dialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await dialog.getByLabel('Введите ЗАПУСТИТЬ DIAGNOSTICS').fill('ЗАПУСТИТЬ DIAGNOSTICS')
  await dialog.getByLabel('Пароль').fill('secret')
  await dialog.getByLabel('Код TOTP или восстановления').fill('123456')
  await dialog.getByRole('button', { name: 'Запустить' }).click()
  await expect(page.getByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '0')
  await page.reload()
  await expect(page.getByText(acceptedId)).toBeVisible()
  await expect(page.getByRole('progressbar', { name: 'Прогресс операции' })).toHaveAttribute('value', '50')
  await expect(page.getByRole('button', { name: 'Выполнить очистку' })).toBeDisabled()
  await expect(page.getByText('Недоступно на этом хосте')).toHaveCount(10)
})
