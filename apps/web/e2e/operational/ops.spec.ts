import { expect, test } from '@playwright/test'
import { installMockApi } from '../support/mockApi'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { userForRole } from './fixtures'
import { selectInterface } from '../support/interfaceMode'

for (const mode of ['Классический', 'Новый А'] as const) for (const width of [320, 1440]) for (const theme of ['light', 'dark']) {
  test(`Royal system health ${mode} ${width} ${theme}: accessible responsive approval`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.addInitScript(theme => localStorage.setItem('robopark-theme', theme), theme)
    await installMockApi(page, { user: userForRole('royal'), routes: [
      { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
      { method: 'GET', path: '/api/admin/settings/integrations', handler: () => ({ json: {} }) },
      { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: {} }) },
      { method: 'GET', path: '/api/admin/ops/job', handler: () => ({ json: { id: '', state: 'idle' } }) },
      { method: 'GET', path: '/api/admin/ops/system-health', handler: () => ({ json: {
        version: '1.2.0', git_sha: 'a'.repeat(40), generated_at: new Date().toISOString(), overall: 'degraded',
        checks: [{ code: 'tuna_inactive', status: 'failed', message: 'Сервис Tuna', repair: 'restart_tuna' }],
        update: { state: 'rolled_back', publication: 'degraded' }, last_backup: { status: 'success', completed_at: new Date().toISOString() },
      } }) },
      { method: 'GET', path: '/api/admin/ops/available-update', handler: () => ({ json: { state: 'available', checked_at: new Date().toISOString(), release: { release_id: 42, version: '1.3.0', git_sha: 'b'.repeat(40), sha256: 'c'.repeat(64), size: 10485760 } } }) },
    ] })
    await page.goto('/admin/settings?tab=ops')
    await selectInterface(page, mode)
    await expect(page.getByRole('heading', { name: 'Здоровье системы' })).toBeVisible()
    await expect(page.getByText('Сервис Tuna', { exact: true })).toBeVisible()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await assertNoSeriousA11yViolations(page)
    await page.screenshot({ path: `/tmp/task9-${width}-${theme}.png`, fullPage: true })
    const trigger = page.getByRole('button', { name: 'Обновить из GitHub' })
    await trigger.focus()
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Подтвердить обновление' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByLabel('Для GitHub введите ОБНОВИТЬ')).toBeFocused()
    await expect(dialog.getByRole('button', { name: 'Установить версию 1.3.0' })).toBeDisabled()
    await dialog.getByLabel('Для GitHub введите ОБНОВИТЬ').fill('ОБНОВИТЬ')
    await expect(dialog.getByRole('button', { name: 'Установить версию 1.3.0' })).toBeEnabled()
    await assertNoSeriousA11yViolations(page)
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(trigger).toBeFocused()
  })
}

test('accepted diagnostics keeps polling when a delayed pre-dispatch idle GET completes', async ({ page }) => {
  await page.clock.install()
  let releaseIdle!: () => void
  const idlePending = new Promise<void>(resolve => { releaseIdle = resolve })
  let jobReads = 0
  const runningJob = {
    id: 'diagnostics-race', kind: 'diagnostics', state: 'running', phase: 'awaiting_host', log: '',
    error: null, artifact_ready: false, restart_required: false,
    created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    restore_phrase: 'ВОССТАНОВИТЬ', update_phrase: 'ОБНОВИТЬ', host_result: null,
  }
  await installMockApi(page, { user: { ...userForRole('royal'), permissions: ['parks.manage'] }, routes: [
    { method: 'GET', path: '/api/admin/ops/job', handler: async () => {
      if (++jobReads === 1) {
        await idlePending
        return { json: { ...runningJob, id: '', state: 'idle' } }
      }
      return { json: jobReads === 2 ? runningJob : { ...runningJob, state: 'succeeded', artifact_ready: true } }
    } },
    { method: 'POST', path: '/api/admin/ops/diagnostics', handler: () => ({ json: runningJob }) },
    { method: 'GET', path: '/api/admin/ops/system-health', handler: () => ({ json: {
      version: '1.2.0', git_sha: 'a'.repeat(40), generated_at: new Date().toISOString(), overall: 'ok', checks: [],
      update: { state: 'idle', publication: null }, last_backup: { status: 'unknown', completed_at: null },
    } }) },
    { method: 'GET', path: '/api/admin/ops/available-update', handler: () => ({ json: { state: 'disabled', checked_at: null, release: null } }) },
  ] })
  await page.goto('/admin/settings?tab=ops')
  await expect.poll(() => jobReads).toBe(1)
  const start = page.getByRole('button', { name: 'Собрать диагностику' })
  await start.click()
  await expect(page.getByText('Выполняется', { exact: true })).toBeVisible()
  await page.clock.fastForward(2500)
  await expect.poll(() => jobReads).toBe(2)
  releaseIdle()
  await expect(start).toBeDisabled()
  await expect(page.getByText('Выполняется', { exact: true })).toBeVisible()
  await page.clock.fastForward(2500)
  await expect(page.getByText('Завершено', { exact: true })).toBeVisible()
  await expect(start).toBeEnabled()
  await expect(page.getByRole('button', { name: 'Скачать диагностику' })).toBeEnabled()
  expect(jobReads).toBe(3)
})
