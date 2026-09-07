import { afterEach, expect, it, vi } from 'vitest'
import { ApiError, ApiTimeoutError, type DiagnosticRuleCreate } from '../../api'
import { testDiagnosticSamples } from './diagnosticSampleApi'
import { emptyDraft } from './DiagnosticRuleEditor'
const rule: DiagnosticRuleCreate = { ...emptyDraft, pattern: 'SECRET-SIGNAL' }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it.each([{ detail: [{ input: 'PRIVATE-SOURCE' }] }, { detail: 'PRIVATE-SOURCE' }])('does not expose arbitrary API errors', async body => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 422 })))
  await expect(testDiagnosticSamples(rule)).rejects.toEqual(new ApiError(422, null))
})

it('preserves the explicit catalog cost limit error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'diagnostic_sample_catalog_too_large' }), { status: 422 })))
  await expect(testDiagnosticSamples(rule)).rejects.toEqual(new ApiError(422, 'diagnostic_sample_catalog_too_large'))
})

it('cancels retired owners and limits stalled requests without retries', async () => {
  vi.useFakeTimers()
  const signals: AbortSignal[] = []
  vi.stubGlobal('fetch', vi.fn((_url, init: RequestInit) => new Promise((_resolve, reject) => {
    signals.push(init.signal! as AbortSignal)
    init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
  })))
  const owner = new AbortController()
  const cancellation = expect(testDiagnosticSamples(rule, 7, owner.signal)).rejects.toMatchObject({ name: 'AbortError' })
  owner.abort(); await cancellation
  const timeout = expect(testDiagnosticSamples(rule)).rejects.toBeInstanceOf(ApiTimeoutError)
  await vi.advanceTimersByTimeAsync(20_000)
  await timeout
  expect(signals).toHaveLength(2)
  expect(signals.every(signal => signal.aborted)).toBe(true)
})
