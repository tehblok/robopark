import { afterEach, expect, it, vi } from 'vitest'
import { api, ApiError, type DiagnosticRuleCreate } from '../../api'

afterEach(() => vi.unstubAllGlobals())
it('preserves catalog ETag and submits the full reorder with JSON content type and If-Match', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('[]', { headers: { ETag: '"catalog-1"' } }))
  vi.stubGlobal('fetch', fetcher)
  const catalog = await api.diagnosticRules()
  expect(catalog).toEqual({ rules: [], etag: '"catalog-1"' })
  fetcher.mockResolvedValue(new Response('[]', { headers: { ETag: '"catalog-2"' } }))
  expect(await api.reorderDiagnosticRules([2, 1, 3], catalog.etag!)).toEqual({ rules: [], etag: '"catalog-2"' })
  const [path, init] = fetcher.mock.calls.at(-1)!
  expect(path).toBe('/api/admin/diagnostic-rules/reorder')
  expect(init).toMatchObject({ method: 'PUT', credentials: 'include' })
  expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  expect(new Headers(init.headers).get('If-Match')).toBe('"catalog-1"')
  expect(JSON.parse(init.body)).toEqual({ ids: [2, 1, 3] })
})

it('excludes server order and identity even when a complete persisted rule is passed to PATCH', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{}'))
  vi.stubGlobal('fetch', fetcher)
  await api.updateDiagnosticRule(7, { title: 'Лидар', sort_order: 12, id: 7 } as unknown as Partial<DiagnosticRuleCreate>)
  expect(fetcher.mock.calls[0][0]).toBe('/api/admin/diagnostic-rules/7')
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ title: 'Лидар' })
})

it.each([401, 403, 409, 428])('retains diagnostic HTTP %s for session/access and catalog recovery', async status => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":"diagnostic_rules_changed"}', { status })))
  await expect(api.diagnosticRules()).rejects.toMatchObject({ status, name: 'ApiError' })
})

it('never includes nested validation bodies in diagnostic errors', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"detail":[{"input":"SECRET","msg":"SECRET"}]}', { status: 422 })))
  try { await api.diagnosticRules(); expect.unreachable() } catch (error) {
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).detail).toBeNull()
    expect(String(error)).not.toContain('SECRET')
  }
})
