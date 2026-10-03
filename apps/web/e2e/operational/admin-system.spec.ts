import { createHmac, randomBytes, randomInt } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import jsQR from 'jsqr'
import { installOperational, userForRole } from './fixtures'
import type { MockRoute } from '../support/mockApi'
import { startDiagnosticApi } from '../support/diagnosticApi'

function currentTotp(secret: string): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  const bytes: number[] = []
  let value = 0, bits = 0
  for (const char of secret) {
    value = (value << 5) | alphabet.indexOf(char)
    bits += 5
    if (bits >= 8) {
      bits -= 8
      bytes.push((value >>> bits) & 0xff)
    }
  }
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000)))
  const digest = createHmac('sha1', Buffer.from(bytes)).update(counter).digest()
  const offset = digest.at(-1)! & 0x0f
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, '0')
}

const revision = 'a'.repeat(64)
const summary = {
  sampled_at: '2026-09-25T09:00:00Z', metrics_stale: false,
  online: { total: 4, by_role: { mechanic: 3, admin: 1 }, by_park: { '7': 4 } },
  sync: { cursor_age_seconds: 12, pending_action_count: 2, oldest_pending_action_age_seconds: 20, retry_count: 1, needs_attention_count: 1, last_success_at: '2026-09-25T08:59:00Z', last_error: null, worker_lease_state: 'active' },
  push: { pending: 1, needs_attention: 0 },
  metrics: { host: { cpu: { state: 'ok', load_1m: .4, cores: 4 }, disk: { total_bytes: 1000, free_bytes: 400 }, memory: { total_bytes: 1000, available_bytes: 500 }, postgresql: { state: 'ok' }, container: { state: 'ok' }, tuna: { state: 'ok' }, internet: { state: 'ok' }, requests: {}, storage: { bytes_to_reclaim: 0, cleanup_failed: false, space_pressure: true }, backup: { overdue: false } } }, release: { version: '0.2.0-rc.6' },
}
const kinds = ['ota-update', 'rollback', 'package-inspect', 'package-update', 'service-restart', 'reboot', 'backup', 'backup-verify', 'backup-restore', 'cleanup-preview', 'cleanup-execute', 'docker-image-preview', 'docker-image-execute', 'builder-cache-preview', 'builder-cache-execute', 'diagnostics', 'usb-discover', 'usb-format', 'usb-select']
const safe = new Set(['package-inspect', 'backup-verify', 'cleanup-preview', 'cleanup-execute', 'docker-image-preview', 'docker-image-execute', 'builder-cache-preview', 'builder-cache-execute', 'diagnostics', 'usb-discover', 'usb-select'])
const capabilities = { state: 'ready', generated_at: '2026-09-25T09:00:00Z', expires_at: '2099-09-25T09:05:00Z', revision, operations: Object.fromEntries(kinds.map(kind => [kind, { available: safe.has(kind), unavailable_reason: safe.has(kind) ? null : 'capability_unavailable' }])) }
const history = { active_users: [{ date: '2026-09-23', users: 5 }, { date: '2026-09-24', users: 6 }], metrics: [] }

test('phone system can jump to measured storage while leaving the health console mounted', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 })
  await installOperational(page, { user: userForRole('admin'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
  ] })
  await page.goto('/system?park=7')

  const navigation = page.getByRole('navigation', { name: 'Разделы системы' })
  await expect(navigation.getByRole('link', { name: 'Состояние' })).toBeVisible()
  await page.screenshot({ path: '/tmp/robopark-system-navigation-phone.png' })
  await navigation.getByRole('link', { name: 'Занятое место' }).click()
  await expect(page).toHaveURL(/#system-storage$/)
  await expect(page.getByRole('region', { name: 'Занятое место на хосте' })).toBeInViewport()
  await expect(page.getByRole('region', { name: 'Ресурсы' })).toBeVisible()
  await page.screenshot({ path: '/tmp/robopark-system-storage-jump-phone.png' })
})

test('a new owner enrolls TOTP and saves one-time recovery codes on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const secret = Array.from(randomBytes(20), byte => 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'[byte % 32]).join('')
  const password = randomBytes(18).toString('hex')
  const code = String(randomInt(0, 1_000_000)).padStart(6, '0')
  const recoveryCodes = [randomBytes(12).toString('hex'), randomBytes(12).toString('hex')]
  let enrolled = false
  let confirmationAttempts = 0
  const routes: MockRoute[] = [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/privileged-auth/status', handler: () => ({ json: { enrolled } }) },
    { method: 'POST', path: '/api/admin/privileged-auth/enrollment', handler: () => ({ json: { secret } }) },
    { method: 'POST', path: '/api/admin/privileged-auth/enrollment/confirm', handler: async request => {
      const body = await request.json() as Record<string, string>
      expect(body.password === password && body.code === code).toBe(true)
      confirmationAttempts += 1
      if (confirmationAttempts === 1) return { status: 400, json: { detail: 'invalid_totp' } }
      enrolled = true
      return { json: { recovery_codes: recoveryCodes } }
    } },
  ]
  await installOperational(page, { user: userForRole('royal'), routes })
  await page.goto('/system?park=7')
  await page.getByRole('button', { name: 'Настроить TOTP' }).click()
  const qrImage = page.getByRole('img', { name: 'QR для настройки TOTP' })
  await expect(qrImage).toHaveAttribute('src', /^data:image\/svg\+xml/)
  const qrPixels = await qrImage.evaluate(async element => {
    const image = element as HTMLImageElement
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 464
    const context = canvas.getContext('2d')!
    context.drawImage(image, 0, 0, 464, 464)
    return Array.from(context.getImageData(0, 0, 464, 464).data)
  })
  const qrValue = jsQR(new Uint8ClampedArray(qrPixels), 464, 464)?.data
  expect(qrValue?.startsWith('otpauth://totp/')).toBe(true)
  const qrUri = new URL(qrValue!)
  expect(qrUri.searchParams.get('secret') === secret).toBe(true)
  expect(qrUri.searchParams.get('issuer')).toBe('Robopark')
  expect(qrUri.searchParams.get('digits')).toBe('6')
  expect(qrUri.searchParams.get('period')).toBe('30')
  expect(decodeURIComponent(qrUri.pathname.slice(1))).toBe('Robopark:royal-e2e')
  expect(await page.locator('.rp-totp-enrollment__setup code').textContent() === secret).toBe(true)
  await page.getByLabel('Текущий пароль').fill(password)
  await page.getByLabel('Код из приложения').fill(code)
  await page.getByRole('button', { name: 'Подтвердить настройку' }).click()
  await expect(page.getByText('Код истёк или введён неверно. Введите новый код.')).toBeVisible()
  await page.getByRole('button', { name: 'Подтвердить настройку' }).click()
  await expect(page.getByText('Сохраните коды восстановления')).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Скачать коды' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('robopark-recovery-codes.txt')
  const file = await readFile(await download.path(), 'utf8')
  expect(file.split('\n').filter(Boolean)).toHaveLength(3)
  await page.getByRole('button', { name: 'Я сохранил коды' }).click()
  await expect(page.getByText('TOTP настроен')).toBeVisible()
  await expect(page.locator('html')).toHaveJSProperty('scrollWidth', 390)
})

test('owner completes TOTP enrollment against the real API from a phone', async ({ page }) => {
  const bridge = await startDiagnosticApi('royal')
  try {
    const privilegedRoutes: MockRoute[] = (['GET', 'POST'] as const).map(method => ({
      method,
      path: /^\/api\/admin\/privileged-auth(?:\/|$)/,
      handler: async request => {
        const url = new URL(request.url)
        return bridge.call({
          method: request.method,
          path: url.pathname.replace(/^\/api/, '') + url.search,
          body: request.method === 'GET' ? undefined : await request.text(),
          headers: { 'content-type': 'application/json' },
        })
      },
    }))
    await page.setViewportSize({ width: 390, height: 844 })
    await installOperational(page, {
      user: { ...userForRole('royal'), username: 'royal-browser' },
      routes: [
        { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
        { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
        { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
        ...privilegedRoutes,
      ],
    })
    await page.goto('/system?park=7')
    await page.getByRole('button', { name: 'Настроить TOTP' }).click()
    const secret = await page.locator('.rp-totp-enrollment__setup code').textContent()
    expect(secret?.length).toBe(32)
    const qrPixels = await page.getByRole('img', { name: 'QR для настройки TOTP' }).evaluate(async element => {
      const image = element as HTMLImageElement
      await image.decode()
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 464
      const context = canvas.getContext('2d')!
      context.drawImage(image, 0, 0, 464, 464)
      return Array.from(context.getImageData(0, 0, 464, 464).data)
    })
    const qrValue = jsQR(new Uint8ClampedArray(qrPixels), 464, 464)?.data
    expect(qrValue?.startsWith('otpauth://totp/')).toBe(true)
    expect(new URL(qrValue!).searchParams.get('secret') === secret).toBe(true)
    await page.getByLabel('Текущий пароль').fill(bridge.password)
    await page.getByLabel('Код из приложения').fill(currentTotp(secret!))
    await page.getByRole('button', { name: 'Подтвердить настройку' }).click()
    await expect(page.getByText('Сохраните коды восстановления')).toBeVisible()
    await expect(page.locator('.rp-recovery-codes li')).toHaveCount(10)
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Скачать коды' }).click()
    const download = await downloadPromise
    const saved = await readFile(await download.path(), 'utf8')
    expect(saved.split('\n').filter(Boolean)).toHaveLength(11)
    await page.getByRole('button', { name: 'Я сохранил коды' }).click()
    await page.reload()
    await expect(page.getByText('TOTP настроен')).toBeVisible()
    await expect(page.getByText('Сохраните коды восстановления')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  } finally {
    await bridge.close()
  }
})

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
  await expect(page.getByRole('table', { name: 'Активные пользователи за 7 дней — значения' })).toContainText('24.09.2026')
  await expect(page.getByText('Wi‑Fi')).toBeVisible()
  await expect(page.getByText('Место: недостаточно')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Управляемые операции' })).toHaveCount(0)
  await expect(page.locator('body')).not.toContainText(/argv|командная строка/i)
  await expect(page.locator('html')).toHaveJSProperty('scrollWidth', 390)
})

test('host read failures are visible on a phone instead of looking like empty metrics', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const failedSummary = { ...summary, metrics_stale: true, worker_health: 'worker_metric_missing', metrics: { host: {
    ...summary.metrics.host,
    disk: { total_bytes: null, free_bytes: null, source_state: 'unavailable' },
    host_health_source_state: 'unavailable',
    storage: { ...summary.metrics.host.storage, bytes_to_reclaim: null, space_pressure: null, cleanup_failed: null, builder_cache_budget: { attempted: true, blocked: true } },
  } } }
  await installOperational(page, { user: userForRole('admin'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: failedSummary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
  ] })
  await page.goto('/system?park=7')
  await expect(page.getByText('Метрики устарели. Сведения о ресурсах могут быть неактуальны; проверьте сбор метрик worker.')).toBeVisible()
  await expect(page.getByText('Метрика worker отсутствует после запуска. Проверьте сбор метрик и журнал worker.')).toBeVisible()
  await expect(page.getByText('Не удалось измерить диск API. Проверьте доступность каталога данных на хосте.')).toBeVisible()
  await expect(page.getByText('Снимок host agent отсутствует или повреждён. Проверьте службу host agent и её журнал.')).toBeVisible()
  await expect(page.getByText('Ограничение кэша сборки Docker не выполнено. Проверьте журнал host agent и доступность собственного BuildKit builder Robopark.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Ресурсы' }).getByText('Ошибка чтения')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Очереди и интеграции' })).toContainText('Объём к освобождению не измерен')
  await expect(page.getByText('Место: недостаточно')).toHaveCount(0)
  await expect(page.getByText('Очистка: Неизвестно')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-system-host-errors-phone.png', fullPage: true })
})

test('future worker metric is reported as a clock error on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { user: userForRole('admin'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: {
      ...summary, worker_health: 'worker_metric_future', metrics_stale: true,
    } }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
  ] })
  await page.goto('/system?park=7')
  await expect(page.getByText('Время метрики worker опережает сервер. Проверьте часы хоста и сбор метрик.')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Очереди и интеграции' })).toContainText('Ошибка времени метрики')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.screenshot({ path: '/tmp/robopark-system-future-metric-phone.png', fullPage: true })
})

test('owner previews exact Robopark Docker image tags on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const tag = 'robopark-web:11111111-1111-4111-8111-111111111111'
  await installOperational(page, { user: userForRole('royal'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/privileged-auth/status', handler: () => ({ json: { enrolled: true } }) },
    { method: 'POST', path: '/api/admin/privileged-auth/reauthorize', handler: () => ({ json: { token: 'reauth', expires_in: 120 } }) },
    { method: 'POST', path: '/api/admin/ops/operations', handler: async request => {
      const body = await request.json() as Record<string, unknown>
      expect(body.kind).toBe('docker-image-preview')
      return { json: {
        id: body.operation_id, kind: 'docker-image-preview', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
        host_result: { docker_image_preview: { plan_id: '11111111-1111-4111-8111-111111111111', blocked: false, planned: [{ tag, reported_bytes: 4096 }], total_reported_bytes: 4096, unverified_tags: 1 } },
      } }
    } },
  ] })
  await page.goto('/system?park=7')
  await page.getByRole('button', { name: 'Просмотреть образы Docker' }).click()
  const dialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await dialog.getByLabel('Введите ЗАПУСТИТЬ DOCKER-IMAGE-PREVIEW').fill('ЗАПУСТИТЬ DOCKER-IMAGE-PREVIEW')
  await dialog.getByLabel('Пароль').fill('secret')
  await dialog.getByLabel('Код TOTP или восстановления').fill('123456')
  await dialog.getByRole('button', { name: 'Запустить' }).click()
  await expect(page.getByText(tag)).toBeVisible()
  await expect(page.getByText(/непроверенных меток: 1/i)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Выполнить очистку' })).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Удалить показанные образы' })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.locator('.rp-system-cleanup-preview').screenshot({ path: '/tmp/robopark-system-docker-images-phone.png' })
  await page.getByRole('button', { name: 'Удалить показанные образы' }).locator('..').screenshot({ path: '/tmp/robopark-system-docker-image-action-phone.png' })
  await page.getByRole('button', { name: 'Удалить показанные образы' }).click()
  const approval = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await expect(approval.getByText(tag)).toBeVisible()
  await approval.screenshot({ path: '/tmp/robopark-system-docker-image-approval-phone.png' })
})

test('owner previews one exact private BuildKit record on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const password = randomBytes(18).toString('hex')
  const code = String(randomInt(0, 1_000_000)).padStart(6, '0')
  const authorizationToken = randomBytes(24).toString('hex')
  const planId = '11111111-1111-4111-8111-111111111111'
  const recordId = 'z'.repeat(26)
  await installOperational(page, { user: userForRole('royal'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/privileged-auth/status', handler: () => ({ json: { enrolled: true } }) },
    { method: 'POST', path: '/api/admin/privileged-auth/reauthorize', handler: async request => {
      const body = await request.json() as Record<string, string>
      expect(body.password === password && body.code === code).toBe(true)
      return { json: { token: authorizationToken, expires_in: 120 } }
    } },
    { method: 'POST', path: '/api/admin/ops/operations', handler: async request => {
      const body = await request.json() as Record<string, unknown>
      expect(body.kind).toBe('builder-cache-preview')
      return { json: {
        id: body.operation_id, kind: 'builder-cache-preview', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
        host_result: { builder_cache_preview: {
          plan_id: planId, blocked: false, planned: [{ id: recordId, reported_bytes: 8192 }],
          total_reported_bytes: 8192, other_candidates: 2,
        } },
      } }
    } },
  ] })
  await page.goto('/system?park=7')
  await page.getByRole('button', { name: 'Просмотреть кэш BuildKit' }).click()
  const dialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await dialog.getByLabel('Введите ЗАПУСТИТЬ BUILDER-CACHE-PREVIEW').fill('ЗАПУСТИТЬ BUILDER-CACHE-PREVIEW')
  await dialog.getByLabel('Пароль').fill(password)
  await dialog.getByLabel('Код TOTP или восстановления').fill(code)
  await dialog.getByRole('button', { name: 'Запустить' }).click()
  await expect(page.getByText(/ещё кандидатов: 2/i)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Очистить показанную запись BuildKit' })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.getByRole('button', { name: 'Очистить показанную запись BuildKit' }).click()
  const approval = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await expect(approval.getByText(recordId)).toBeVisible()
  await approval.screenshot({ path: '/tmp/robopark-system-builder-cache-approval-phone.png' })
})

test('owner sees the exact terminal OTA cache target before cleanup on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const digest = 'a'.repeat(64)
  const operationId = '11111111-1111-4111-8111-111111111111'
  await installOperational(page, { user: userForRole('royal'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/privileged-auth/status', handler: () => ({ json: { enrolled: true } }) },
    { method: 'POST', path: '/api/admin/privileged-auth/reauthorize', handler: () => ({ json: { token: 'reauth', expires_in: 120 } }) },
    { method: 'POST', path: '/api/admin/ops/operations', handler: async request => {
      const body = await request.json() as Record<string, unknown>
      if (body.kind === 'cleanup-execute') {
        expect(body.plan_id).toBe(operationId)
        return { json: {
          id: body.operation_id, kind: 'cleanup-execute', state: 'failed', phase: 'failed',
          progress_percent: 100, error: 'cleanup_partial',
          host_result: { cleanup_result: {
            deleted: [{ category: 'ota_cache', path: `${digest}.ota`, bytes: 4096 }],
            deleted_count: 1, uncertain_target: null,
          } },
        } }
      }
      expect(body.categories).toEqual(['ota_cache'])
      return { json: {
        id: operationId, kind: 'cleanup-preview', state: 'succeeded', phase: 'completed', progress_percent: 100, error: null,
        host_result: { cleanup_preview: { plan_id: operationId, blocked: false, total_bytes: 4096,
          planned: [{ category: 'ota_cache', path: `${digest}.ota`, bytes: 4096 }] } },
      } }
    } },
  ] })
  await page.goto('/system?park=7')
  const operations = page.getByRole('region', { name: 'Управляемые операции' })
  await expect(operations.getByRole('checkbox', { name: 'Резервные копии' })).not.toBeChecked()
  await expect(operations.getByRole('checkbox', { name: 'Релизы' })).not.toBeChecked()
  await operations.getByRole('checkbox', { name: 'Диагностика' }).uncheck()
  await operations.getByRole('checkbox', { name: 'Журналы Robopark' }).uncheck()
  await operations.locator('.rp-system-cleanup-categories').screenshot({ path: '/tmp/robopark-system-cleanup-categories-phone.png' })
  await page.getByRole('button', { name: 'Предпросмотр очистки' }).click()
  const dialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await dialog.getByLabel('Введите ЗАПУСТИТЬ CLEANUP-PREVIEW').fill('ЗАПУСТИТЬ CLEANUP-PREVIEW')
  await dialog.getByLabel('Пароль').fill('secret')
  await dialog.getByLabel('Код TOTP или восстановления').fill('123456')
  await dialog.getByRole('button', { name: 'Запустить' }).click()
  await expect(page.getByText(`/var/lib/robopark/ops/state/ota-packages/${digest}.ota`)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Выполнить очистку' })).toBeEnabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.locator('.rp-system-cleanup-preview').screenshot({ path: '/tmp/robopark-system-ota-cache-preview-phone.png' })
  await page.getByRole('button', { name: 'Выполнить очистку' }).click()
  const executeDialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await executeDialog.getByLabel('Введите CLEAN ROBOPARK').fill('CLEAN ROBOPARK')
  await executeDialog.getByLabel('Пароль').fill('secret')
  await executeDialog.getByLabel('Код TOTP или восстановления').fill('123456')
  await executeDialog.getByRole('button', { name: 'Запустить' }).click()
  await expect(page.getByText(/Очистка прервана\. Удалённые файлы: 1\./)).toBeVisible()
  await page.getByRole('region', { name: 'Текущая операция' }).screenshot({ path: '/tmp/robopark-system-cleanup-partial-phone.png' })
})

test('owner sees a blocked OTA cache scan as an error, not an empty cache', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { user: userForRole('royal'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/privileged-auth/status', handler: () => ({ json: { enrolled: true } }) },
    { method: 'POST', path: '/api/admin/privileged-auth/reauthorize', handler: () => ({ json: { token: 'reauth', expires_in: 120 } }) },
    { method: 'POST', path: '/api/admin/ops/operations', handler: () => ({ json: {
      id: '22222222-2222-4222-8222-222222222222', kind: 'cleanup-preview', state: 'succeeded', phase: 'completed',
      progress_percent: 100, error: null, host_result: { cleanup_preview: {
        plan_id: '22222222-2222-4222-8222-222222222222', blocked: true, total_bytes: 0, planned: [],
      } },
    } }) },
  ] })
  await page.goto('/system?park=7')
  const operations = page.getByRole('region', { name: 'Управляемые операции' })
  await operations.getByRole('checkbox', { name: 'Диагностика' }).uncheck()
  await operations.getByRole('checkbox', { name: 'Журналы Robopark' }).uncheck()
  await page.getByRole('button', { name: 'Предпросмотр очистки' }).click()
  const dialog = page.getByRole('dialog', { name: 'Подтвердить операцию' })
  await dialog.getByLabel('Введите ЗАПУСТИТЬ CLEANUP-PREVIEW').fill('ЗАПУСТИТЬ CLEANUP-PREVIEW')
  await dialog.getByLabel('Пароль').fill(randomBytes(18).toString('hex'))
  await dialog.getByLabel('Код TOTP или восстановления').fill(String(randomInt(0, 1_000_000)).padStart(6, '0'))
  await dialog.getByRole('button', { name: 'Запустить' }).click()
  await expect(page.getByText('План заблокирован проверкой безопасности. Очистка недоступна.')).toBeVisible()
  await expect(page.getByText('Безопасных кандидатов для очистки нет.')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Выполнить очистку' })).toBeDisabled()
})

test('the first day of system activity is readable without a lone chart bar', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await installOperational(page, { user: userForRole('admin'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: { active_users: [{ date: '2026-09-24', users: 6 }], metrics: [] } }) },
  ] })
  await page.goto('/system?park=7')
  const overview = page.getByRole('region', { name: 'Пользователи' })
  await expect(overview.getByText('История за один день. График появится после следующего дня.')).toBeVisible()
  await expect(overview.getByRole('img', { name: 'Активные пользователи за 7 дней' })).toHaveCount(0)
  await expect(overview.getByRole('table', { name: 'Активные пользователи за 7 дней — значения' })).toContainText('24.09.2026')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await overview.screenshot({ path: '/tmp/robopark-system-first-day-phone.png' })
})

test('owner can read a critically small measured disk balance on a phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ colorScheme: 'dark' })
  const lowSpace = {
    ...summary,
    metrics: { host: {
      ...summary.metrics.host,
      disk: { total_bytes: 64 * 1024 ** 3, free_bytes: 20 * 1024 ** 2 },
      memory: { total_bytes: 4 * 1024 ** 3, available_bytes: 512 * 1024 ** 2 },
      storage: { ...summary.metrics.host.storage, bytes_to_reclaim: 20 * 1024 ** 2 },
    } },
  }
  await installOperational(page, { user: userForRole('admin'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: lowSpace }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
  ] })
  await page.goto('/system?park=7')
  const resources = page.getByRole('region', { name: 'Ресурсы' })
  await expect(resources.getByText('20 МБ свободно')).toBeVisible()
  await expect(resources.getByText('>99%')).toBeVisible()
  await expect(page.getByText('К очистке: 20 МБ')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  const storage = page.getByRole('region', { name: 'Занятое место на хосте' })
  const missing = storage.locator('.rp-system-storage-missing')
  await expect(missing.locator('summary')).toHaveText('Не измерено: 17 категорий')
  await expect(missing.locator('.rp-system-storage-breakdown')).toBeHidden()
  await missing.locator('summary').click()
  const storageRows = missing.locator('.rp-system-storage-breakdown > div')
  const firstStorageRow = await storageRows.nth(0).boundingBox()
  const secondStorageRow = await storageRows.nth(1).boundingBox()
  expect(firstStorageRow && secondStorageRow && secondStorageRow.y >= firstStorageRow.y + firstStorageRow.height).toBe(true)
  await resources.screenshot({ path: '/tmp/robopark-system-low-disk-phone.png' })
})

test('owner sees the owned BuildKit estimate apart from host-wide Docker totals', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const mebibyte = 1024 ** 2
  const measured = { ...summary, metrics: { host: {
    ...summary.metrics.host,
    storage: { ...summary.metrics.host.storage, category_bytes: {
      buildkit_cache: 900 * mebibyte,
      robopark_buildkit_reported: 320 * mebibyte,
      robopark_buildkit_private_reclaimable: 96 * mebibyte,
    } },
  } } }
  await installOperational(page, { user: userForRole('royal'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: measured }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/privileged-auth/status', handler: () => ({ json: { enrolled: true } }) },
  ] })
  await page.goto('/system?park=7')
  const storage = page.getByRole('region', { name: 'Занятое место на хосте' })
  await expect(storage.getByText('BuildKit Robopark (отчётный объём)')).toBeVisible()
  await expect(storage.getByText('320 МБ')).toBeVisible()
  await expect(storage.getByText('96 МБ')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await storage.getByText('BuildKit Robopark (отчётный объём)').scrollIntoViewIfNeeded()
  await page.screenshot({ path: '/tmp/robopark-owned-buildkit-phone.png' })
})

test('desktop resource cards and sparse activity chart remain readable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const gibibyte = 1024 ** 3
  const mebibyte = 1024 ** 2
  const measuredSummary = {
    ...summary, worker_health: 'worker_healthy',
    metrics: { host: {
      ...summary.metrics.host,
      disk: { total_bytes: 64 * gibibyte, free_bytes: 18 * gibibyte },
      memory: { total_bytes: 4 * gibibyte, available_bytes: 2 * gibibyte },
      wifi: { state: 'ok' },
      requests: { tracker: { errors: 0 } },
      backup: { verified_at: 1, overdue: false },
      storage: { ...summary.metrics.host.storage, space_pressure: false, category_bytes: {
        logs: 160 * mebibyte, diagnostics: 80 * mebibyte, live_merge: 12 * mebibyte,
        report_attachments: 620 * mebibyte, tracker_uploads: 350 * mebibyte,
        backups: 2 * gibibyte, scheduled_backups: 4 * gibibyte, releases: 3 * gibibyte,
        ota_uploads: 0, ota_cache: 0, journald: 180 * mebibyte,
        docker_images: 9 * gibibyte, buildkit_cache: 2 * gibibyte,
        docker_volumes: 8 * gibibyte, postgresql_data: 6 * gibibyte,
      } },
    } },
  }
  await installOperational(page, { user: userForRole('admin'), routes: [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: measuredSummary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
  ] })
  await page.goto('/system?park=7')
  const value = page.locator('.rp-system-grid').first().locator('.rp-metric-card').filter({ has: page.getByText('PostgreSQL', { exact: true }) }).locator('.rp-metric-card__value')
  await expect(value).toHaveText('Работает')
  const lines = await value.evaluate(element => { const range = document.createRange(); range.selectNodeContents(element); return range.getClientRects().length })
  expect(lines).toBe(1)
  const storageRows = page.locator('.rp-system-storage-breakdown > div')
  const firstStorageRow = await storageRows.nth(0).boundingBox()
  const secondStorageRow = await storageRows.nth(1).boundingBox()
  expect(firstStorageRow && secondStorageRow && Math.abs(firstStorageRow.y - secondStorageRow.y) < 2).toBe(true)
  const chart = await page.getByRole('img', { name: 'Активные пользователи за 7 дней' }).boundingBox()
  const bars = page.getByRole('img', { name: 'Активные пользователи за 7 дней' }).locator('rect')
  await expect(bars).toHaveCount(2)
  const firstBar = await bars.nth(0).boundingBox()
  const secondBar = await bars.nth(1).boundingBox()
  expect(chart && firstBar && secondBar).toBeTruthy()
  expect(chart!.height).toBeLessThanOrEqual(160)
  expect(firstBar!.x + firstBar!.width < chart!.x + chart!.width / 2).toBe(true)
  expect(secondBar!.x > chart!.x + chart!.width / 2).toBe(true)
  await page.screenshot({ path: '/tmp/robopark-system-desktop.png', fullPage: true })
})

test('royal confirms an available typed operation and resumes UUID progress at 1440px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  let acceptedId = ''
  const routes: MockRoute[] = [
    { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
    { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: history }) },
    { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: capabilities }) },
    { method: 'GET', path: '/api/admin/privileged-auth/status', handler: () => ({ json: { enrolled: true } }) },
    { method: 'GET', path: /^\/api\/admin\/ops\/operations\/[0-9a-f-]{36}$/, handler: request => {
      const requestedId = new URL(request.url).pathname.split('/').at(-1)
      return requestedId === acceptedId
        ? { json: { id: acceptedId, kind: 'diagnostics', state: 'running', phase: 'executing', progress_percent: 50, error: null } }
        : { status: 404, json: { detail: 'operation_not_found' } }
    } },
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
  await page.getByText('Недоступные действия (7)').click()
  await expect(page.getByRole('button', { name: 'Выполнить очистку' })).toBeDisabled()
  await expect(page.locator('html')).toHaveJSProperty('scrollWidth', 1440)
})
