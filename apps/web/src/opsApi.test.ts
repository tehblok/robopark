import { afterEach, expect, it, vi } from 'vitest'
import { api } from './api'
import { systemClient } from './opsApi'

afterEach(() => vi.unstubAllGlobals())
it('sends inspected archive separately from strict JSON approvals and uses dedicated artifacts', async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    requests.push({ url, init })
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
  })
  await api.opsSystemHealth()
  await api.opsAvailableUpdate()
  await api.opsInspectUpdate(new File(['zip'], 'signed.zip'))
  await api.opsApproveUpdate('inspection-one', 'ОБНОВИТЬ')
  await api.opsApproveGithubUpdate(42, 'ОБНОВИТЬ')
  await api.opsRepair()
  await api.opsDiagnosticArtifact()
  await systemClient.getOperation('11111111-1111-4111-8111-111111111111')
  expect(requests.map(({ url }) => url)).toEqual([
    '/api/admin/ops/system-health', '/api/admin/ops/available-update', '/api/admin/ops/update/inspect',
    '/api/admin/ops/update/approve', '/api/admin/ops/github-update/approve',
    '/api/admin/ops/repair', '/api/admin/ops/diagnostic-artifact',
    '/api/admin/ops/operations/11111111-1111-4111-8111-111111111111',
  ])
  const form = requests[2].init?.body as FormData
  expect(form.get('archive')).toBeInstanceOf(File)
  expect(form.has('confirm')).toBe(false)
  expect(JSON.parse(String(requests[3].init?.body))).toEqual({ inspection_id: 'inspection-one', confirm: 'ОБНОВИТЬ' })
  expect(JSON.parse(String(requests[4].init?.body))).toEqual({ release_id: 42, confirm: 'ОБНОВИТЬ' })
  expect(requests.slice(2, 6).every(({ init }) => init?.method === 'POST')).toBe(true)
})
