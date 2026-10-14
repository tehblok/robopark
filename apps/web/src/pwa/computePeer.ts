const MAX_TRANSFER = 8 * 1024 * 1024
const MAX_SIGNAL = 80 * 1024
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function channelEvent(channel: RTCDataChannel, event: 'open' | 'bufferedamountlow', signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const clean = () => { channel.removeEventListener(event, success); channel.removeEventListener('close', fail); channel.removeEventListener('error', fail); signal.removeEventListener('abort', fail) }
    const success = () => { clean(); resolve() }
    const fail = () => { clean(); reject(new Error('peer_channel_closed')) }
    channel.addEventListener(event, success, { once: true }); channel.addEventListener('close', fail, { once: true }); channel.addEventListener('error', fail, { once: true }); signal.addEventListener('abort', fail, { once: true })
    if (signal.aborted || channel.readyState === 'closed' || channel.readyState === 'closing') fail()
    else if ((event === 'open' && channel.readyState === 'open') || (event === 'bufferedamountlow' && channel.bufferedAmount <= channel.bufferedAmountLowThreshold)) success()
  })
}

export async function sendPeerBytes(channel: RTCDataChannel, bytes: Uint8Array, signal: AbortSignal): Promise<void> {
  if (bytes.length < 1 || bytes.length > MAX_TRANSFER) throw new Error('peer_size_invalid')
  channel.bufferedAmountLowThreshold = 64 * 1024
  await channelEvent(channel, 'open', signal)
  channel.send(JSON.stringify({ type: 'begin', bytes: bytes.length }))
  for (let offset = 0; offset < bytes.length; offset += 16 * 1024) {
    if (signal.aborted || channel.readyState !== 'open') throw new Error('peer_channel_closed')
    if (channel.bufferedAmount > 256 * 1024) await channelEvent(channel, 'bufferedamountlow', signal)
    channel.send(bytes.slice(offset, offset + 16 * 1024))
  }
  channel.send(JSON.stringify({ type: 'end' }))
}

export function receivePeerBytes(channel: RTCDataChannel, signal: AbortSignal): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    let bytes: Uint8Array | undefined
    let offset = 0
    const clean = () => { channel.removeEventListener('message', message); channel.removeEventListener('close', fail); channel.removeEventListener('error', fail); signal.removeEventListener('abort', fail) }
    const fail = () => { clean(); reject(new Error('peer_transfer_invalid')) }
    const message = (event: MessageEvent) => {
      try {
        if (typeof event.data === 'string') {
          if (event.data.length > 256) return fail()
          const control = JSON.parse(event.data)
          if (control.type === 'begin' && !bytes && Number.isInteger(control.bytes) && control.bytes > 0 && control.bytes <= MAX_TRANSFER) bytes = new Uint8Array(control.bytes)
          else if (control.type === 'end' && bytes && offset === bytes.length) { clean(); resolve(bytes) }
          else fail()
        } else if (Object.prototype.toString.call(event.data) === '[object ArrayBuffer]' && bytes && event.data.byteLength > 0 && event.data.byteLength <= 16 * 1024 && offset + event.data.byteLength <= bytes.length) {
          bytes.set(new Uint8Array(event.data), offset); offset += event.data.byteLength
        } else fail()
      } catch { fail() }
    }
    channel.binaryType = 'arraybuffer'
    channel.addEventListener('message', message); channel.addEventListener('close', fail, { once: true }); channel.addEventListener('error', fail, { once: true }); signal.addEventListener('abort', fail, { once: true })
    if (signal.aborted) fail()
  })
}

async function gatheredOffer(peer: RTCPeerConnection, signal: AbortSignal): Promise<RTCSessionDescriptionInit> {
  await peer.setLocalDescription(await peer.createOffer())
  if (peer.iceGatheringState !== 'complete') await new Promise<void>((resolve, reject) => {
    const clean = () => { clearTimeout(timer); peer.removeEventListener('icegatheringstatechange', change); signal.removeEventListener('abort', abort) }
    const finish = () => { clean(); resolve() }
    const abort = () => { clean(); reject(new Error('peer_cancelled')) }
    const change = () => { if (peer.iceGatheringState === 'complete') finish() }
    const timer = setTimeout(finish, 3000)
    peer.addEventListener('icegatheringstatechange', change); signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
    else change()
  })
  const offer = peer.localDescription
  if (!offer || offer.type !== 'offer' || offer.sdp.length > 64 * 1024) throw new Error('peer_offer_invalid')
  return { type: 'offer', sdp: offer.sdp }
}

async function signalResponse(response: Response): Promise<Record<string, unknown>> {
  if (!response.ok || !response.body) throw new Error('peer_unavailable')
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let size = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > MAX_SIGNAL) throw new Error('peer_signal_size')
      chunks.push(value)
    }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock() }
  const merged = new Uint8Array(size); let offset = 0
  for (const chunk of chunks) { merged.set(chunk, offset); offset += chunk.byteLength }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(merged)) as Record<string, unknown>
}

function base64(bytes: Uint8Array): string {
  let value = ''
  for (let i = 0; i < bytes.length; i += 8192) value += String.fromCharCode(...bytes.subarray(i, i + 8192))
  return btoa(value)
}

/** Only SDP, IDs and hashes pass through the server. Payload/result use DTLS DataChannel. */
export type PeerJobOptions = { signal?: AbortSignal, timeoutMs?: number, onFailure?: (reason: 'unauthorized' | 'cancelled') => void }
export async function requestPeerJob(operation: string, payload: Record<string, unknown>, validate: (result: Record<string, unknown>) => Promise<boolean> = async () => true, options: PeerJobOptions = {}): Promise<Record<string, unknown> | null> {
  if (operation !== 'image_resize_v1' || navigator.onLine === false || typeof RTCPeerConnection === 'undefined') return null
  const input = new TextEncoder().encode(JSON.stringify(payload))
  if (input.length > 6 * 1024 * 1024) return null
  const controller = new AbortController()
  let peer: RTCPeerConnection | undefined
  let channel: RTCDataChannel | undefined
  let jobID: string | undefined
  let completed = false
  let cancelled = false
  let timer: ReturnType<typeof setTimeout> | undefined
  const sessionChanged = () => { cancelled = true; options.onFailure?.('cancelled'); controller.abort(); channel?.close(); peer?.close() }
  window.addEventListener('robopark:compute-cancel', sessionChanged)
  window.addEventListener('offline', sessionChanged)
  options.signal?.addEventListener('abort', sessionChanged, { once: true })
  try {
    if (options.signal?.aborted) { sessionChanged(); return null }
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', input)), byte => byte.toString(16).padStart(2, '0')).join('')
    if (controller.signal.aborted) return null
    return await Promise.race([
      new Promise<null>(resolve => { timer = setTimeout(() => { controller.abort(); peer?.close(); resolve(null) }, Math.max(1, Math.min(options.timeoutMs ?? 30_000, 30_000))) }),
      (async () => {
        peer = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.cloudflare.com:3478' }] })
        channel = peer.createDataChannel('robopark-compute-v1', { ordered: true })
        const offer = await gatheredOffer(peer, controller.signal)
        if (controller.signal.aborted) return null
        const response = await fetch('/api/compute/direct', {
          method: 'POST', credentials: 'include', cache: 'no-store', signal: controller.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ operation, payload_sha256: digest, payload_bytes: input.length, offer }),
        })
        if (response.status === 401 || response.status === 403) options.onFailure?.('unauthorized')
        const lease = await signalResponse(response)
        if (controller.signal.aborted) return null
        if (typeof lease.job_id !== 'string' || !UUID.test(lease.job_id)) throw new Error('peer_lease_invalid')
        jobID = lease.job_id
        const answer = lease.answer as RTCSessionDescriptionInit | undefined
        if (typeof lease.attempt_id !== 'string' || !UUID.test(lease.attempt_id)
          || typeof lease.generation !== 'string' || !UUID.test(lease.generation)
          || typeof lease.token !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(lease.token)
          || !answer || answer.type !== 'answer' || typeof answer.sdp !== 'string' || answer.sdp.length > 64 * 1024
          || !Number.isInteger(lease.timeout_ms) || Number(lease.timeout_ms) < 1 || Number(lease.timeout_ms) > 30_000) throw new Error('peer_lease_invalid')
        await peer.setRemoteDescription(answer)
        await channelEvent(channel, 'open', controller.signal)
        const frame = { type: 'compute_job', generation: lease.generation, job_id: jobID, attempt_id: lease.attempt_id,
          operation, payload_base64: base64(input), payload_sha256: digest, timeout_ms: lease.timeout_ms }
        const receiving = receivePeerBytes(channel, controller.signal)
        const [, raw] = await Promise.all([sendPeerBytes(channel, new TextEncoder().encode(JSON.stringify({ token: lease.token, frame })), controller.signal), receiving])
        const result = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw))
        if (result.type !== 'compute_result' || result.generation !== lease.generation || result.job_id !== jobID
          || result.attempt_id !== lease.attempt_id || result.payload_sha256 !== digest || result.ok !== true
          || !result.result || typeof result.result !== 'object' || Array.isArray(result.result)) return null
        if (!await validate(result.result) || controller.signal.aborted) return null
        // The authoritative server fences revocation before the caller uses a peer result.
        const accepted = await fetch(`/api/compute/direct/${encodeURIComponent(jobID)}/finish`, {
          method: 'POST', credentials: 'include', cache: 'no-store', signal: controller.signal,
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ outcome: 'completed' }),
        })
        if (accepted.status === 401 || accepted.status === 403) options.onFailure?.('unauthorized')
        if (!accepted.ok || controller.signal.aborted) return null
        channel.send(JSON.stringify({ type: 'ack', job_id: jobID, attempt_id: lease.attempt_id,
          payload_sha256: digest, outcome: 'completed' }))
        // Let the final small acknowledgement drain before closing the DTLS transport.
        channel.bufferedAmountLowThreshold = 0
        if (channel.bufferedAmount > 0) await channelEvent(channel, 'bufferedamountlow', controller.signal)
        completed = true
        return result.result as Record<string, unknown>
      })(),
    ])
  } catch { return null }
  finally {
    window.removeEventListener('robopark:compute-cancel', sessionChanged)
    window.removeEventListener('offline', sessionChanged)
    options.signal?.removeEventListener('abort', sessionChanged)
    clearTimeout(timer); controller.abort(); channel?.close(); peer?.close()
    if (jobID && !completed && !cancelled) {
      const cancel = new AbortController(); const timeout = setTimeout(() => cancel.abort(), 2000)
      await fetch(`/api/compute/direct/${encodeURIComponent(jobID)}/finish`, { method: 'POST', credentials: 'include', cache: 'no-store', signal: cancel.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ outcome: cancelled ? 'cancelled' : 'fallback' }) })
        .catch(() => undefined).finally(() => clearTimeout(timeout))
    }
  }
}
