import { ApiError, ApiTimeoutError, type DiagnosticRuleCreate } from '../../api'

export type DiagnosticSampleResult = {
  items: { id: number; outcome: 'matched' | 'missed' | 'skipped'; overlap_rule_ids: number[]; reason: 'legacy' | 'unusable' | 'budget' | null }[]
  matched: number; missed: number; skipped: number; overlapping: number
  limit: number; has_more: boolean; budget_exhausted: boolean; invalid_rule_ids: number[]
}
const safeCodes = new Set(['invalid_diagnostic_source_path', 'invalid_diagnostic_regex', 'unsupported_diagnostic_regex', 'diagnostic_sample_catalog_too_large'])

export async function testDiagnosticSamples(rule: DiagnosticRuleCreate, excludeRuleId?: number, signal?: AbortSignal): Promise<DiagnosticSampleResult> {
  const controller = new AbortController()
  const abort = () => controller.abort(signal?.reason)
  if (signal?.aborted) abort()
  else signal?.addEventListener('abort', abort, { once: true })
  let timedOut = false
  const timer = globalThis.setTimeout(() => { timedOut = true; controller.abort() }, 20_000)
  try {
    const response = await fetch('/api/admin/diagnostic-rules/test-samples', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      signal: controller.signal, body: JSON.stringify({ rule, limit: 50, exclude_rule_id: excludeRuleId }),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: unknown } | null
      throw new ApiError(response.status, typeof body?.detail === 'string' && safeCodes.has(body.detail) ? body.detail : null, response.headers.get('X-Request-ID') || undefined)
    }
    return await response.json() as DiagnosticSampleResult
  } catch (error) {
    if (timedOut) throw new ApiTimeoutError(20_000)
    throw error
  } finally {
    globalThis.clearTimeout(timer)
    signal?.removeEventListener('abort', abort)
  }
}
