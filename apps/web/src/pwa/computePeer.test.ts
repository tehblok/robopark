import { afterEach, describe, expect, it, vi } from 'vitest'
import { receivePeerBytes, requestPeerJob, sendPeerBytes } from './computePeer'

class Channel extends EventTarget {
  readyState = 'open'
  bufferedAmount = 0
  bufferedAmountLowThreshold = 0
  binaryType = 'arraybuffer'
  sent: (string | Uint8Array)[] = []
  peer?: Channel
  send(value: string | Uint8Array) {
    this.sent.push(value)
    if (this.peer) queueMicrotask(() => this.peer?.dispatchEvent(new MessageEvent('message', { data: typeof value === 'string' ? value : value.slice().buffer })))
  }
  close() { this.readyState = 'closed'; this.dispatchEvent(new Event('close')) }
  rtc() { return this as unknown as RTCDataChannel }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('bounded direct peer transfer', () => {
  it('transfers bytes in <=16KiB ordered chunks without a server', async () => {
    const left = new Channel(); const right = new Channel(); left.peer = right
    const controller = new AbortController()
    const input = new Uint8Array(500_003).map((_, i) => i % 251)
    const receive = receivePeerBytes(right.rtc(), controller.signal)
    await sendPeerBytes(left.rtc(), input, controller.signal)
    expect(await receive).toEqual(input)
    expect(left.sent.filter(value => typeof value !== 'string').every(value => value.length <= 16384)).toBe(true)
  })
  it.each(['too_large', 'truncated', 'oversized_chunk', 'duplicate_header'])('rejects %s rather than accepting incomplete data', async kind => {
    const channel = new Channel()
    const received = receivePeerBytes(channel.rtc(), new AbortController().signal)
    const assertion = expect(received).rejects.toThrow('peer_transfer_invalid')
    const message = (data: unknown) => channel.dispatchEvent(new MessageEvent('message', { data }))
    message(JSON.stringify({ type: 'begin', bytes: kind === 'too_large' ? 8 * 1024 * 1024 + 1 : 20_000 }))
    if (kind === 'truncated') message(JSON.stringify({ type: 'end' }))
    if (kind === 'oversized_chunk') message(new ArrayBuffer(20_000))
    if (kind === 'duplicate_header') message(JSON.stringify({ type: 'begin', bytes: 20_000 }))
    await assertion
  })
  it('releases a backpressure wait on cancellation', async () => {
    const channel = new Channel(); channel.bufferedAmount = 300_000
    const abort = new AbortController()
    const sending = sendPeerBytes(channel.rtc(), new Uint8Array(100_000), abort.signal)
    const assertion = expect(sending).rejects.toThrow('peer_channel_closed')
    await Promise.resolve(); abort.abort()
    await assertion
    expect(channel.sent.filter(value => typeof value !== 'string')).toHaveLength(0)
  })
  it('does not attempt an HTTP payload fallback when WebRTC is unavailable', async () => {
    vi.stubGlobal('RTCPeerConnection', undefined)
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    expect(await requestPeerJob('image_resize_v1', { data_base64: 'private-image' })).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })
  it.each(['completed', 'invalid_result', 'revoked'])('sends only signaling metadata and fences %s', async outcome => {
    const browser = new Channel(); const worker = new Channel(); browser.peer = worker; worker.peer = browser
    const lease = { job_id: '11111111-1111-4111-8111-111111111111', attempt_id: '22222222-2222-4222-8222-222222222222', generation: '33333333-3333-4333-8333-333333333333', token: 'a'.repeat(43), timeout_ms: 20_000, answer: { type: 'answer', sdp: 'test answer' } }
    class Peer extends EventTarget {
      iceGatheringState = 'complete'
      localDescription = { type: 'offer', sdp: 'test offer' }
      createDataChannel = () => browser.rtc()
      createOffer = async () => this.localDescription
      setLocalDescription = async () => undefined
      setRemoteDescription = async () => undefined
      close = vi.fn()
    }
    vi.stubGlobal('RTCPeerConnection', Peer)
    const fetch = vi.fn().mockImplementation(async (_url) => String(_url).endsWith('/finish') ? new Response(null, { status: outcome === 'revoked' ? 403 : 204 }) : new Response(JSON.stringify(lease)))
    vi.stubGlobal('fetch', fetch)
    const server = (async () => {
      const raw = await receivePeerBytes(worker.rtc(), new AbortController().signal)
      const input = JSON.parse(new TextDecoder().decode(raw))
      expect(input.token).toBe(lease.token)
      const response = { ...input.frame, type: 'compute_result', ok: true, result: { processed: true } }
      await sendPeerBytes(worker.rtc(), new TextEncoder().encode(JSON.stringify(response)), new AbortController().signal)
    })()
    const payload = { data_base64: 'private-image'.repeat(30000) }
    expect(await requestPeerJob('image_resize_v1', payload, async () => outcome !== 'invalid_result')).toEqual(outcome === 'completed' ? { processed: true } : null)
    await server
    const metadata = JSON.parse(fetch.mock.calls[0][1].body)
    expect(metadata).toMatchObject({ operation: 'image_resize_v1', offer: { type: 'offer' } })
    expect(metadata.payload_bytes).toBeGreaterThan(300000)
    expect(fetch.mock.calls[0][1].body.length).toBeLessThan(1024)
    expect(fetch.mock.calls[0][1].body).not.toContain('private-image')
    expect(fetch.mock.calls[1][1].method).toBe('POST')
    if (outcome === 'completed') {
      expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ outcome: 'completed' })
      expect(fetch).toHaveBeenCalledTimes(2)
      expect(browser.sent.some(value => typeof value === 'string' && JSON.parse(value).type === 'ack')).toBe(true)
    } else {
      expect(JSON.parse(fetch.mock.calls.at(-1)![1].body)).toEqual({ outcome: 'fallback' })
      expect(browser.sent.some(value => typeof value === 'string' && JSON.parse(value).type === 'ack')).toBe(false)
    }
  })
})
