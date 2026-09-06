import { ApiError, ApiTimeoutError, type DiagnosticRule, type DiagnosticRuleCreate, type JsonValue } from '../../api'

export type UnknownDiagnosticState = 'new' | 'mapped' | 'ignored'
export type UnknownDiagnostic = {
  id: number; source_path: string; raw_value: JsonValue; original_value: JsonValue; pattern: string
  first_seen_at: string; last_seen_at: string; observations: number; last_robot: string
  state: UnknownDiagnosticState; rule_id: number | null
}
export type UnknownDiagnosticPage = { items: UnknownDiagnostic[]; total: number; limit: number; offset: number; has_more: boolean }

// Deliberately exclude validation input and arbitrary response text from errors.
const safeCodes = new Set(['invalid_diagnostic_source_path', 'invalid_diagnostic_regex', 'unsupported_diagnostic_regex', 'diagnostic_preview_source_too_large', 'diagnostic_unknown_already_mapped', 'unknown_sample_requires_observation', 'unknown_rule_does_not_match', 'diagnostic_rule_conflict'])
async function request<T>(suffix: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const abort = () => controller.abort(init.signal?.reason)
  if (init.signal?.aborted) abort()
  else init.signal?.addEventListener('abort', abort, { once: true })
  let timedOut = false
  const timer = globalThis.setTimeout(() => { timedOut = true; controller.abort() }, 20_000)
  try {
    const response = await fetch(`/api/admin/diagnostic-unknowns${suffix}`, { ...init, credentials: 'include', headers: { 'Content-Type': 'application/json' }, signal: controller.signal })
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: unknown } | null
      throw new ApiError(response.status, typeof body?.detail === 'string' && safeCodes.has(body.detail) ? body.detail : null, response.headers.get('X-Request-ID') || undefined)
    }
    return await response.json() as T
  } catch (error) {
    if (timedOut) throw new ApiTimeoutError(20_000)
    throw error
  } finally {
    globalThis.clearTimeout(timer)
    init.signal?.removeEventListener('abort', abort)
  }
}
export const unknownDiagnosticApi = {
  list: (state: UnknownDiagnosticState, offset = 0, signal?: AbortSignal) => request<UnknownDiagnosticPage>(`?state=${state}&limit=50&offset=${offset}`, { signal }),
  get: (id: number, signal?: AbortSignal) => request<UnknownDiagnostic>(`/${id}`, { signal }),
  classify: (id: number, rule: DiagnosticRuleCreate) => request<DiagnosticRule>(`/${id}/classify`, { method: 'POST', body: JSON.stringify({ rule }) }),
  ignore: (id: number) => request<UnknownDiagnostic>(`/${id}/ignore`, { method: 'POST' }),
  reopen: (id: number) => request<UnknownDiagnostic>(`/${id}/reopen`, { method: 'POST' }),
}
