import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, ApiTimeoutError } from '../../api'
import { unknownDiagnosticApi } from './unknownDiagnosticApi'
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
it('keeps structured validation input out of thrown errors', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: [{ input: 'PRIVATE_SOURCE' }] }), { status: 422 })))
  await expect(unknownDiagnosticApi.list('new')).rejects.toMatchObject({ status: 422, detail: null })
})
it('preserves status and known conflict codes for access and mutation handling', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'diagnostic_unknown_already_mapped' }), { status: 409 })))
  await expect(unknownDiagnosticApi.ignore(7)).rejects.toEqual(new ApiError(409, 'diagnostic_unknown_already_mapped'))
})
it('forwards cancellation to fetch and bounds stalled requests', async () => {
  vi.useFakeTimers()
  const signals: AbortSignal[] = []
  vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
    signals.push(init.signal! as AbortSignal)
    init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  })))
  const owner = new AbortController()
  const cancelled = unknownDiagnosticApi.list('new', 0, owner.signal)
  const cancellation = expect(cancelled).rejects.toMatchObject({ name: 'AbortError' })
  owner.abort(); await cancellation
  expect(signals[0].aborted).toBe(true)
  const stalled = expect(unknownDiagnosticApi.list('new')).rejects.toBeInstanceOf(ApiTimeoutError)
  await vi.advanceTimersByTimeAsync(20_000)
  await stalled
  expect(signals[1].aborted).toBe(true)
})
