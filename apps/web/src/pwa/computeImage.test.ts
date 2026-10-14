import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { transformOnMac } from './computeImage'
import { requestPeerJob } from './computePeer'
import { jpegBase64 } from './__fixtures__/photo'
vi.mock('./computePeer', () => ({ requestPeerJob: vi.fn() }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.mocked(requestPeerJob).mockReset() })
const photo = () => new File([new Uint8Array(300_000)], 'photo.png', { type: 'image/png' })
async function result() {
  const bytes = Uint8Array.from(atob(jpegBase64), c => c.charCodeAt(0))
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join('')
  return { data_base64: btoa(String.fromCharCode(...bytes)), sha256: hash, mime_type: 'image/jpeg', width: 2, height: 2 }
}
describe('direct cross-Mac photo processing', () => {
  beforeEach(() => {
    vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 2, height: 2, close: vi.fn() }))
  })
  function respond(reply: Record<string, unknown> | null) {
    vi.mocked(requestPeerJob).mockImplementation(async (_operation, _payload, validate) => reply && await validate!(reply) ? reply : null)
  }
  it('uses a hash-validated image received directly from a free Mac', async () => {
    respond(await result())
    const output = await transformOnMac(photo(), 1920, 0.82)
    expect(output?.type).toBe('image/jpeg')
    expect(output?.size).toBe(atob(jpegBase64).length)
    expect(requestPeerJob).toHaveBeenCalledWith('image_resize_v1', expect.objectContaining({ mime_type: 'image/png', max_edge: 1920, quality: 82 }), expect.any(Function))
  })
  it.each(['bad_hash', 'oversize_dimension', 'wrong_type', 'bad_magic', 'no_worker', 'lying_dimensions', 'decode_failure', 'decoded_mismatch'])('preserves original on %s', async reason => {
    const reply = await result()
    if (reason === 'bad_hash') reply.sha256 = '0'.repeat(64)
    if (reason === 'oversize_dimension') reply.width = 20000
    if (reason === 'wrong_type') reply.mime_type = 'text/html'
    if (reason === 'bad_magic') reply.data_base64 = btoa('<script>')
    if (reason === 'lying_dimensions') reply.width = 100
    if (reason === 'decode_failure') vi.mocked(createImageBitmap).mockRejectedValue(new Error('invalid JPEG'))
    if (reason === 'decoded_mismatch') vi.mocked(createImageBitmap).mockResolvedValue({ width: 3, height: 2, close: vi.fn() } as unknown as ImageBitmap)
    respond(reason === 'no_worker' ? null : reply)
    expect(await transformOnMac(photo(), 1920, 0.82)).toBeNull()
  })
  it('keeps offline, tiny and oversized photos on the source device', async () => {
    expect(await transformOnMac(new File(['small'], 'a.jpg', { type: 'image/jpeg' }), 1920, 0.82)).toBeNull()
    expect(await transformOnMac(new File([new Uint8Array(4 * 1024 * 1024 + 1)], 'a.jpg', { type: 'image/jpeg' }), 1920, 0.82)).toBeNull()
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    expect(await transformOnMac(photo(), 1920, 0.82)).toBeNull()
    expect(requestPeerJob).not.toHaveBeenCalled()
  })
  it('keeps the original on direct channel failure without sending it through server Wi-Fi', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    vi.mocked(requestPeerJob).mockRejectedValue(new Error('NAT traversal failed'))
    expect(await transformOnMac(photo(), 1920, 0.82)).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })
})
