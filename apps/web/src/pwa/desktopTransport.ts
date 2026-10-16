import { desktopRuntime } from './desktopRuntime'

const SESSION_KEY = 'robopark:desktop-session:v1'
const CAPABILITY = /^[a-f0-9]{64}$/
const SESSION_HEADER = 'X-Robopark-Session'
const LAUNCH_HEADER = 'X-Robopark-Launch'
let restoreFetch: (() => void) | undefined
let pendingInitialization: Promise<void> | undefined

export class DesktopSessionRequiredError extends TypeError {
  constructor() {
    super('Откройте Robopark через приложение, чтобы восстановить связь этой вкладки с локальной службой.')
    this.name = 'DesktopSessionRequiredError'
  }
}

// HTTP cookies do not isolate localhost ports. Only explicit requests to this
// exact origin carry the owner-session capability handed off by the native app.
// It survives service updates/restarts and rotates on account changes/revocation.
export function initializeDesktopTransport(): Promise<void> {
  if (!desktopRuntime() || (restoreFetch && !pendingInitialization)) return Promise.resolve()
  if (pendingInitialization) return pendingInitialization
  const initialization = installDesktopTransport()
  const tracked = initialization.finally(() => {
    if (pendingInitialization === tracked) pendingInitialization = undefined
  })
  pendingInitialization = tracked
  return pendingInitialization
}

async function installDesktopTransport(): Promise<void> {
  const origin = window.location.origin
  const originalFetch = globalThis.fetch
  let capability: string | null = null
  let authGeneration = 0
  try { capability = sessionStorage.getItem(SESSION_KEY) } catch { /* memory-only session */ }
  if (!capability || !CAPABILITY.test(capability)) capability = null
  const save = (value: string | null) => {
    capability = value
    try {
      if (value) sessionStorage.setItem(SESSION_KEY, value)
      else sessionStorage.removeItem(SESSION_KEY)
    } catch { /* private browsers can keep the capability in memory */ }
  }
  globalThis.fetch = async (input, init) => {
    const source = input instanceof Request ? input : null
    const url = new URL(source ? source.url : String(input), window.location.href)
    const headers = new Headers(init?.headers ?? source?.headers)
    // Do not carry these private headers across origins, including localhost ports.
    headers.delete(SESSION_HEADER)
    headers.delete(LAUNCH_HEADER)
    const local = url.origin === origin && (url.pathname.startsWith('/api/') || url.pathname.startsWith('/__robopark/'))
    if (!local) return originalFetch(input, { ...init, headers })
    if (capability) headers.set(SESSION_HEADER, capability)
    const login = url.pathname === '/api/auth/login'
    const logout = url.pathname === '/api/auth/logout'
    const generation = login || logout ? ++authGeneration : authGeneration
    try {
      const response = await originalFetch(input, { ...init, headers, credentials: 'omit', redirect: 'error' })
      if (response.status === 401 && !login) {
        const body = await response.clone().json().catch(() => null) as { detail?: unknown } | null
        if (response.headers.get('X-Robopark-Local-Session') === 'required' || body?.detail === 'local_session_required') {
          // A stale tab capability is not an upstream account revocation. Do
          // not broadcast logout or purge another tab's newly verified drafts.
          save(null)
          throw new DesktopSessionRequiredError()
        }
      }
      if (login && generation === authGeneration) {
        const granted = response.headers.get(SESSION_HEADER)
        save(response.ok && granted && CAPABILITY.test(granted) ? granted : null)
      } else if (logout && generation === authGeneration && response.ok) save(null)
      return response
    } catch (error) {
      if (login && generation === authGeneration) save(null)
      throw error
    }
  }
  restoreFetch = () => { globalThis.fetch = originalFetch; restoreFetch = undefined }
  const fragment = window.location.hash
  if (!fragment.startsWith('#rp-launch=')) return
  // Remove the one-use launch nonce before rendering links or loading user data.
  window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
  const nonce = fragment.slice('#rp-launch='.length)
  save(null)
  if (!CAPABILITY.test(nonce)) return
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), 10_000)
  try {
    const response = await originalFetch('/__robopark/bootstrap', {
      method: 'POST', headers: { [LAUNCH_HEADER]: nonce }, credentials: 'omit', redirect: 'error',
      cache: 'no-store', signal: controller.signal,
    })
    if (!response.ok) return
    const body = await response.json() as { session?: unknown; fragment?: unknown }
    if (typeof body.session !== 'string' || !CAPABILITY.test(body.session)) return
    save(body.session)
    if (typeof body.fragment === 'string' && body.fragment.length <= 2048) {
      const target = new URL(window.location.href)
      target.hash = body.fragment
      window.history.replaceState(window.history.state, '', target)
    }
  } catch { /* The login page remains available if a launch expired or the app stopped. */ }
  finally { globalThis.clearTimeout(timeout) }
}

export function resetDesktopTransportForTests(): void {
  if (import.meta.env.MODE !== 'test') throw new Error('desktop_transport_test_override_forbidden')
  restoreFetch?.()
  pendingInitialization = undefined
}
