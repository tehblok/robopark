import { expect, test } from '@playwright/test'
import { installOperational, userForRole } from './fixtures'

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
  push: { pending: 0, needs_attention: 0 }, metrics: { host: {} }, release: { version: '0.2.0-rc.11' },
}

for (const width of [390, 1440]) {
  test(`owner can inspect host actions and download diagnostics at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' })
    const operationId = '11111111-1111-4111-8111-111111111111'
    await installOperational(page, { user: userForRole('royal'), routes: [
      { method: 'GET', path: '/api/admin/system/summary', handler: () => ({ json: summary }) },
      { method: 'GET', path: '/api/admin/system/history', handler: () => ({ json: { active_users: [], metrics: [] } }) },
      { method: 'GET', path: '/api/admin/ops/capabilities', handler: () => ({ json: {
        state: 'ready', generated_at: '2026-09-30T12:00:00Z', expires_at: '2099-09-30T12:05:00Z', revision: 'a'.repeat(64),
        operations: Object.fromEntries(kinds.map(kind => [kind, { available: true, unavailable_reason: null }])),
      } }) },
      { method: 'GET', path: '/api/admin/ops/operation-context', handler: () => ({ json: {
        generated_at: '2026-09-30T12:00:00Z', expires_at: '2099-09-30T12:05:00Z',
        rollback_release: '0.2.0-rc.10', selected_device_uuid: '44444444-4444-4444-8444-444444444444',
        packages: ['docker-ce', 'docker-ce-cli', 'containerd.io', 'openssl'], services: ['robopark.service', 'robopark-tuna.service', 'docker.service'],
        devices: [{ device_uuid: '44444444-4444-4444-8444-444444444444', removable: true, mounted: false }],
        backups: [{ backup_id: '55555555-5555-4555-8555-555555555555', bytes: 8192, verified: true, created_at: '2026-09-30T10:00:00Z' }],
      } }) },
      { method: 'GET', path: '/api/admin/ops/operations', handler: () => ({ json: { items: [
        { id: operationId, kind: 'diagnostics', receipt_state: 'terminal', state: 'succeeded', phase: 'completed', error: null, progress_percent: 100, artifact_ready: true, created_at: '2026-09-30T11:00:00Z', updated_at: '2026-09-30T11:01:00Z', host_result: { diagnostics_ready: true } },
        { id: '22222222-2222-4222-8222-222222222222', kind: 'package-inspect', receipt_state: 'terminal', state: 'succeeded', phase: 'completed', error: null, progress_percent: 100, artifact_ready: false, created_at: '2026-09-30T10:00:00Z', updated_at: '2026-09-30T10:01:00Z', host_result: { package_result: { package: 'openssl', installed: true, version: '3.0.13', updated: null } } },
      ] } }) },
      { method: 'GET', path: `/api/admin/ops/operations/${operationId}/artifact`, handler: () => ({ body: 'zip' }) },
    ] })

    await page.goto('/system?park=all')
    await page.getByRole('link', { name: 'Обслуживание' }).click()
    const operations = page.getByRole('region', { name: 'Управляемые операции' })
    await expect(operations.getByRole('button', { name: 'Откатить версию' })).toBeEnabled()
    await expect(operations.getByRole('button', { name: 'Создать резервную копию' })).toBeEnabled()
    await expect(page.getByText('openssl · версия 3.0.13')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Скачать диагностику' })).toHaveAttribute('href', `/api/admin/ops/operations/${operationId}/artifact`)
    await page.locator('#system-operations').screenshot({ path: testInfo.outputPath(`system-operations-${width}.png`) })
    const overflow = await page.evaluate(() => {
      const viewportWidth = document.documentElement.clientWidth
      const elements = Array.from(document.querySelectorAll<HTMLElement>('body *'))
      const nameOf = (element: HTMLElement) => `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ''}${Array.from(element.classList).slice(0, 4).map(name => `.${name}`).join('')}`
      const offenders = elements.flatMap(element => {
        const rect = element.getBoundingClientRect()
        if (rect.right <= viewportWidth + 0.5 && rect.left >= -0.5) return []
        const style = getComputedStyle(element)
        return [{
          element: nameOf(element),
          left: Math.round(rect.left * 100) / 100,
          right: Math.round(rect.right * 100) / 100,
          width: Math.round(rect.width * 100) / 100,
          clientWidth: element.clientWidth,
          scrollWidth: element.scrollWidth,
          boxSizing: style.boxSizing,
          display: style.display,
          gridTemplateColumns: style.gridTemplateColumns,
          minInlineSize: style.minInlineSize,
          overflowX: style.overflowX,
        }]
      }).sort((left, right) => right.right - left.right || left.width - right.width).slice(0, 20)
      const scrollContainers = elements.flatMap(element => element.scrollWidth > element.clientWidth + 1 ? [{
        element: nameOf(element),
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        overflowX: getComputedStyle(element).overflowX,
      }] : []).sort((left, right) => right.scrollWidth - right.clientWidth - (left.scrollWidth - left.clientWidth)).slice(0, 20)
      const pseudoElements = elements.flatMap(element => (['::before', '::after'] as const).flatMap(pseudo => {
        const style = getComputedStyle(element, pseudo)
        return style.content !== 'none' ? [{
          element: `${nameOf(element)}${pseudo}`,
          content: style.content,
          display: style.display,
          position: style.position,
          inset: style.inset,
          width: style.width,
          transform: style.transform,
        }] : []
      }))
      const rootMetrics = [document.documentElement, document.body].map(element => {
        const rect = element.getBoundingClientRect()
        const style = getComputedStyle(element)
        return {
          element: element.tagName.toLowerCase(),
          clientWidth: element.clientWidth,
          offsetWidth: element.offsetWidth,
          scrollWidth: element.scrollWidth,
          rect: { left: rect.left, right: rect.right, width: rect.width },
          width: style.width,
          minWidth: style.minWidth,
          overflowX: style.overflowX,
        }
      })
      return { documentWidth: document.documentElement.scrollWidth, viewportWidth, rootMetrics, offenders, scrollContainers, pseudoElements }
    })
    expect(overflow.documentWidth, JSON.stringify(overflow, null, 2)).toBeLessThanOrEqual(width)
  })
}
