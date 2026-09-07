import { vi } from 'vitest'

export const healthFixture = {
  version: '1.2.0', git_sha: 'a'.repeat(40), generated_at: new Date().toISOString(),
  overall: 'degraded', checks: [{ code: 'tuna_inactive', status: 'failed', message: 'Сервис Tuna', repair: 'restart_tuna' }],
  update: { state: 'rolled_back', publication: null }, last_backup: { status: 'success', completed_at: new Date(Date.now() - 3600000).toISOString() },
}
export const releaseFixture = { state: 'available', checked_at: new Date().toISOString(), release: { release_id: 42, version: '1.3.0', git_sha: 'b'.repeat(40), size: 10485760, sha256: 'c'.repeat(64) } }
export const inspectionFixture = { inspection_id: 'inspection-one', version: '1.4.0', git_sha: 'd'.repeat(40), migration_head: 'migration_14', notes: 'Улучшена диагностика' }
export function jobFixture(kind = 'diagnostics', state = 'running') {
  return { id: 'job-1', kind, state, phase: 'host_dispatched', log: '', error: null, artifact_ready: state === 'succeeded', restart_required: false, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), restore_phrase: 'ВОССТАНОВИТЬ', update_phrase: 'ОБНОВИТЬ', host_result: null }
}
export function mockOpsServer(overrides: Record<string, unknown | (() => unknown)> = {}) {
  const responses: Record<string, unknown | (() => unknown)> = {
    '/admin/ops/system-health': healthFixture,
    '/admin/ops/available-update': releaseFixture,
    '/admin/ops/job': jobFixture('', 'idle'),
    '/admin/ops/update/inspect': inspectionFixture,
    '/admin/ops/update/approve': jobFixture('update'),
    '/admin/ops/github-update/approve': jobFixture('update'),
    '/admin/ops/diagnostics': jobFixture(),
    '/admin/ops/repair': { ...jobFixture('repair', 'succeeded'), host_result: { before: [], after: [], performed: ['restart_tuna'], failed: ['restart_app'] } },
    ...overrides,
  }
  const fetchMock = vi.fn(async (url: string | URL | Request) => {
    const path = String(url).replace(/^\/api/, '')
    let body = responses[path]
    if (typeof body === 'function') body = await body()
    if (body instanceof Response) return body
    if (body === undefined) throw new Error(`Unexpected request: ${path}`)
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
