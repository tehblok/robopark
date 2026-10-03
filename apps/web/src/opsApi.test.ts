import { afterEach, expect, it, vi } from 'vitest'
import { ApiTimeoutError } from './api'
import { privilegedAuthClient } from './domains/system/privilegedAuthApi'
import { otaUploadClient, systemClient } from './opsApi'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
it('uses only the typed operation status, history and context gateways', async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = []
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    requests.push({ url, init })
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
  })
  await systemClient.getOperation('11111111-1111-4111-8111-111111111111')
  await systemClient.getOperations()
  await systemClient.getOperationContext()
  expect(requests.map(({ url }) => url)).toEqual([
    '/api/admin/ops/operations/11111111-1111-4111-8111-111111111111',
    '/api/admin/ops/operations',
    '/api/admin/ops/operation-context',
  ])
  expect(requests.every(request => request.init?.method === undefined)).toBe(true)
  expect(systemClient.operationArtifactUrl('11111111-1111-4111-8111-111111111111')).toBe(
    '/api/admin/ops/operations/11111111-1111-4111-8111-111111111111/artifact',
  )
})

it.each([
  ['system', () => systemClient.getSummary()],
  ['TOTP', () => privilegedAuthClient.status()],
])('bounds an unresponsive %s API request', async (_label, request) => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  })))
  const result = request()
  const rejection = expect(result).rejects.toBeInstanceOf(ApiTimeoutError)
  await vi.advanceTimersByTimeAsync(15_000)
  await rejection
})

it('bounds an unresponsive OTA upload status request', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  })))
  const result = otaUploadClient.offset('upload-id')
  let failure: unknown = null
  void result.catch(error => { failure = error })
  await vi.advanceTimersByTimeAsync(120_000)
  expect(failure).toBeInstanceOf(ApiTimeoutError)
})
