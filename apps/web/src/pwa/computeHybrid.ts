import { requestPeerJob } from './computePeer'

export type ComputeRoute = 'auto' | 'direct' | 'relay'
let directRetryAt = 0
let relayRetryAt = 0
let directFailures = 0
let epoch = 0
let authorizationDenied = false
const active = new Set<AbortController>()
function resetRoutes() { directRetryAt = 0; relayRetryAt = 0; directFailures = 0 }
function cancelJobs() { epoch++; active.forEach(job => job.abort()); resetRoutes() }
window.addEventListener('robopark:compute-cancel', () => { cancelJobs(); authorizationDenied = false })
window.addEventListener('offline', cancelJobs)
window.addEventListener('online', resetRoutes)

async function boundedJSON(response: Response, maxBytes: number, signal: AbortSignal): Promise<Record<string, unknown>> {
  if (!response.body || signal.aborted) throw new Error('compute_cancelled')
  const reader = response.body.getReader()
  const abort = () => { void reader.cancel().catch(() => undefined) }
  signal.addEventListener('abort', abort, { once: true })
  const chunks: Uint8Array[] = []
  let size = 0
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > maxBytes) throw new Error('compute_response_too_large')
      chunks.push(value)
    }
    if (signal.aborted) throw new Error('compute_cancelled')
    const bytes = new Uint8Array(size); let offset = 0
    for (const part of chunks) { bytes.set(part, offset); offset += part.length }
    const parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('compute_invalid_result')
    return parsed
  } finally { signal.removeEventListener('abort', abort); await reader.cancel().catch(() => undefined); reader.releaseLock() }
}

/** Only bounded, retryable computation. Authoritative mutations never use this router. */
export async function requestHybridJob(operation: string, payload: Record<string, unknown>, validate: (value: Record<string, unknown>) => Promise<boolean>, route: ComputeRoute = 'auto'): Promise<Record<string, unknown> | null> {
  if (operation !== 'image_resize_v1' || navigator.onLine === false || active.size >= 2 || authorizationDenied) return null
  const controller = new AbortController(); active.add(controller)
  const scope = epoch
  const valid = () => scope === epoch && !controller.signal.aborted && navigator.onLine !== false && !authorizationDenied
  const deny = () => { if (scope === epoch) { authorizationDenied = true; active.forEach(job => job.abort()) } }
  const timer = setTimeout(() => controller.abort(), 35_000)
  let ticket: string | undefined
  let denied = false
  try {
    if (route !== 'relay' && Date.now() >= directRetryAt) {
      const reply = await requestPeerJob(operation, payload, validate, {
        signal: controller.signal, timeoutMs: 12_000,
        onFailure: reason => { if (reason === 'unauthorized') deny(); if (reason === 'unauthorized' || reason === 'cancelled') denied = true },
      })
      if (!valid() || denied) return null
      if (reply) { directFailures = 0; directRetryAt = 0; return reply }
      directRetryAt = Date.now() + Math.min(60_000, 10_000 * 2 ** Math.min(directFailures++, 3))
    }
    if (route === 'direct' || !valid() || Date.now() < relayRetryAt) return null
    const body = JSON.stringify(payload)
    const bytes = new TextEncoder().encode(body)
    if (bytes.length > 6 * 1024 * 1024) return null
    const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join('')
    if (!valid()) return null
    const admission = await fetch('/api/compute/relay/admit', {
      method: 'POST', credentials: 'include', cache: 'no-store', signal: controller.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation, payload_bytes: bytes.length, payload_sha256: hash }),
    })
    if (!admission.ok) {
      if (admission.status === 401 || admission.status === 403) { deny(); return null }
      const seconds = Number(admission.headers.get('Retry-After'))
      if (valid()) relayRetryAt = Date.now() + (Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 300) * 1000 : 10_000)
      return null
    }
    // Read with the operation signal; never start the payload after logout/offline.
    const accepted = await boundedJSON(admission, 4096, controller.signal)
    if (typeof accepted.ticket !== 'string' || !/^[A-Za-z0-9_-]{20,128}$/.test(accepted.ticket)
      || !Number.isInteger(accepted.expires_in_ms) || Number(accepted.expires_in_ms) < 1 || Number(accepted.expires_in_ms) > 30_000) return null
    ticket = accepted.ticket
    if (!valid()) return null
    const response = await fetch('/api/compute/image', {
      method: 'POST', credentials: 'include', cache: 'no-store', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-Robopark-Relay-Ticket': ticket }, body,
    })
    if (response.status === 401 || response.status === 403) deny()
    if (!response.ok || !valid()) return null
    const result = await boundedJSON(response, 6 * 1024 * 1024, controller.signal)
    return valid() && await validate(result) && valid() ? result : null
  } catch { return null }
  finally {
    clearTimeout(timer); controller.abort(); active.delete(controller)
    // Expiry handles a lost admission response. Never issue cleanup under a new account.
    if (ticket && scope === epoch) {
      const cleanup = new AbortController(); const deadline = setTimeout(() => cleanup.abort(), 2000)
      try { await fetch(`/api/compute/relay/${encodeURIComponent(ticket)}`, { method: 'DELETE', credentials: 'include', cache: 'no-store', signal: cleanup.signal }) }
      catch { /* The broker's TTL also releases abandoned reservations. */ }
      finally { clearTimeout(deadline) }
    }
  }
}
