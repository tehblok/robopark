import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { requestPeerJob } from './computePeer'
import { requestHybridJob } from './computeHybrid'

vi.mock('./computePeer', () => ({ requestPeerJob: vi.fn() }))
const payload = { data_base64: 'image', mime_type: 'image/png', max_edge: 1920, quality: 82 }
const result = { data_base64: 'processed', sha256: 'a'.repeat(64) }
const ticket = 't'.repeat(43)
let requests: Array<{ url: string, init?: RequestInit }>
const validate = async (value: Record<string, unknown>) => value.data_base64 === 'processed'

beforeEach(() => {
  requests = []
  window.dispatchEvent(new Event('robopark:compute-cancel'))
  vi.mocked(requestPeerJob).mockReset().mockResolvedValue(null)
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    requests.push({ url, init })
    if (url.endsWith('/admit')) return new Response(JSON.stringify({ ticket, expires_in_ms: 30_000 }))
    if (init?.method === 'DELETE') return new Response(null, { status: 204 })
    return new Response(JSON.stringify(result))
  }))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('automatic direct / admitted relay routing', () => {
  it('uses direct bytes with no relay traffic when a peer works', async () => {
    vi.mocked(requestPeerJob).mockResolvedValue(result)
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toEqual(result)
    expect(requests).toEqual([])
  })
  it('admits exact payload bytes/hash before one upload after direct failure', async () => {
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toEqual(result)
    expect(requests.map(r => r.url)).toEqual(['/api/compute/relay/admit', '/api/compute/image', `/api/compute/relay/${ticket}`])
    const body = String(requests[1].init?.body)
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body))), x => x.toString(16).padStart(2, '0')).join('')
    expect(JSON.parse(String(requests[0].init?.body))).toEqual({ operation: 'image_resize_v1', payload_bytes: new TextEncoder().encode(body).length, payload_sha256: hash })
    expect(requests[1].init?.headers).toMatchObject({ 'X-Robopark-Relay-Ticket': ticket })
  })
  it('sends no payload and respects Retry-After when budget / workers unavailable', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response('{}', { status: 503, headers: { 'Retry-After': '30' } }))
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toBeNull()
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it('never relays after a direct permission denial', async () => {
    vi.mocked(requestPeerJob).mockImplementation(async (_op, _data, _check, options) => { options?.onFailure?.('unauthorized'); return null })
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toBeNull()
    expect(requests).toEqual([])
  })
  it('keeps direct-only denial across the later relay attempt and network recovery', async () => {
    vi.mocked(requestPeerJob).mockImplementation(async (_op, _data, _check, options) => { options?.onFailure?.('unauthorized'); return null })
    expect(await requestHybridJob('image_resize_v1', payload, validate, 'direct')).toBeNull()
    window.dispatchEvent(new Event('offline'))
    window.dispatchEvent(new Event('online'))
    expect(await requestHybridJob('image_resize_v1', payload, validate, 'relay')).toBeNull()
    expect(requests).toEqual([])
    window.dispatchEvent(new Event('robopark:compute-cancel'))
    expect(await requestHybridJob('image_resize_v1', payload, validate, 'relay')).toEqual(result)
  })
  it.each([401, 403])('blocks retries after relay admission denial %s until account reset', async status => {
    vi.mocked(fetch).mockResolvedValue(new Response('{}', { status }))
    expect(await requestHybridJob('image_resize_v1', payload, validate, 'relay')).toBeNull()
    window.dispatchEvent(new Event('online'))
    expect(await requestHybridJob('image_resize_v1', payload, validate, 'relay')).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)
  })
  it.each(['robopark:compute-cancel', 'offline'])('fences %s before upload even if preflight completes late', async event => {
    vi.mocked(fetch).mockImplementation(async () => {
      window.dispatchEvent(new Event(event))
      return new Response(JSON.stringify({ ticket, expires_in_ms: 30_000 }))
    })
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toBeNull()
    expect(vi.mocked(fetch).mock.calls.some(([url]) => url === '/api/compute/image')).toBe(false)
  })
  it('rejects an invalid relay result and retains source fallback', async () => {
    expect(await requestHybridJob('image_resize_v1', payload, async () => false)).toBeNull()
  })
  it('does not upload during direct-only work; probes direct again after network recovery', async () => {
    expect(await requestHybridJob('image_resize_v1', payload, validate, 'direct')).toBeNull()
    expect(requests).toEqual([])
    await requestHybridJob('image_resize_v1', payload, validate, 'direct')
    expect(requestPeerJob).toHaveBeenCalledTimes(1)
    window.dispatchEvent(new Event('online'))
    vi.mocked(requestPeerJob).mockResolvedValue(result)
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toEqual(result)
    expect(requestPeerJob).toHaveBeenCalledTimes(2)
  })
  it('keeps an offline device local', async () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    expect(await requestHybridJob('image_resize_v1', payload, validate)).toBeNull()
    expect(requests).toEqual([])
    expect(requestPeerJob).not.toHaveBeenCalled()
  })
})
