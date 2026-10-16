import { afterEach, expect, it, vi } from 'vitest'
import { diagnosticClient } from './deviceClient'

afterEach(() => { vi.unstubAllGlobals() })

it('keeps a diagnostic permission denial local instead of revoking the workspace session', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(
    JSON.stringify({ detail: 'royal_owner_required' }), { status: 403 },
  )))
  const revoked = vi.fn()
  window.addEventListener('robopark:authorization-failure', revoked)
  try {
    await expect(diagnosticClient.snapshot()).rejects.toThrow('desktop_diagnostics_http_403')
    expect(revoked).not.toHaveBeenCalled()
  } finally { window.removeEventListener('robopark:authorization-failure', revoked) }
})

it('still propagates an authoritative expired session', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })))
  const revoked = vi.fn()
  window.addEventListener('robopark:authorization-failure', revoked)
  try {
    await expect(diagnosticClient.snapshot()).rejects.toThrow('desktop_diagnostics_http_401')
    expect(revoked).toHaveBeenCalledOnce()
  } finally { window.removeEventListener('robopark:authorization-failure', revoked) }
})
