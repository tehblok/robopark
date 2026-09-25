import { afterEach, expect, it, vi } from 'vitest'
import { systemClient } from './opsApi'

afterEach(() => vi.unstubAllGlobals())
it('uses only the typed operation status gateway', async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    requests.push({ url, init })
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
  })
  await systemClient.getOperation('11111111-1111-4111-8111-111111111111')
  expect(requests.map(({ url }) => url)).toEqual([
    '/api/admin/ops/operations/11111111-1111-4111-8111-111111111111',
  ])
  expect(requests[0].init?.method).toBeUndefined()
})
