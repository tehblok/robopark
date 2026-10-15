import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setDesktopRuntimeForTests } from './desktopRuntime'
import { DesktopSessionRequiredError, initializeDesktopTransport, resetDesktopTransportForTests } from './desktopTransport'

const token = 'a'.repeat(64)
const nonce = 'b'.repeat(64)
const token2 = 'c'.repeat(64)
const origin = 'http://127.0.0.1:18444'
const key = 'robopark:desktop-session:v1'
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })

describe('desktop browser capability transport', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    vi.stubGlobal('location', new URL(origin))
  })
  afterEach(() => {
    resetDesktopTransportForTests()
    setDesktopRuntimeForTests(undefined)
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('consumes and removes the launch fragment before loading private data, without cookies', async () => {
    vi.stubGlobal('location', new URL(`${origin}/tasks#rp-launch=${nonce}`))
    const replace = vi.spyOn(window.history, 'replaceState').mockImplementation(() => undefined)
    const native = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (input === '/__robopark/bootstrap') {
        expect(replace).toHaveBeenCalledWith(null, '', '/tasks')
        expect(init?.headers).toEqual({ 'X-Robopark-Launch': nonce })
        expect(init?.credentials).toBe('omit')
        return json({ session: token, fragment: 'task=42' })
      }
      return json({})
    })
    vi.stubGlobal('fetch', native)
    await initializeDesktopTransport()
    await fetch('/api/tasks')
    expect(sessionStorage.getItem(key)).toBe(token)
    expect(new Headers(native.mock.calls[1][1]?.headers).get('X-Robopark-Session')).toBe(token)
    expect(native.mock.calls[1][1]).toMatchObject({ credentials: 'omit', redirect: 'error' })
    expect(String(replace.mock.calls[1][2])).toBe(`${origin}/tasks#task=42`)
    expect(document.cookie).not.toContain(token)
  })

  it('never sends capabilities to another port, another host or a redirected response', async () => {
    sessionStorage.setItem(key, token)
    const native = vi.fn(async () => json({}))
    vi.stubGlobal('fetch', native)
    await initializeDesktopTransport()
    for (const url of ['http://127.0.0.1:3000/api/tasks', 'https://other.test/api/tasks']) {
      await fetch(url, { headers: { 'X-Robopark-Session': token, 'X-Robopark-Launch': nonce, Accept: 'image/*' } })
      const headers = new Headers((native.mock.calls.at(-1) as unknown as [unknown, RequestInit])[1].headers)
      expect(headers.has('X-Robopark-Session')).toBe(false)
      expect(headers.has('X-Robopark-Launch')).toBe(false)
      expect(headers.get('Accept')).toBe('image/*')
    }
    await fetch(new Request(`${origin}/api/tasks`, { headers: { Accept: 'application/json' } }))
    const init = (native.mock.calls.at(-1) as unknown as [unknown, RequestInit])[1]
    expect(new Headers(init.headers).get('X-Robopark-Session')).toBe(token)
    expect(init.redirect).toBe('error')
  })

  it('rotates after verified login, clears on logout and ignores a stale login response', async () => {
    let completeLogin!: (response: Response) => void
    const native = vi.fn(async (input: RequestInfo | URL) => input === '/api/auth/login'
      ? new Promise<Response>(resolve => { completeLogin = resolve }) : new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', native)
    await initializeDesktopTransport()
    const login = fetch('/api/auth/login', { method: 'POST' })
    completeLogin(new Response(null, { status: 204, headers: { 'X-Robopark-Session': token2 } }))
    await login
    expect(sessionStorage.getItem(key)).toBe(token2)
    const stale = fetch('/api/auth/login', { method: 'POST' })
    await fetch('/api/auth/logout', { method: 'POST' })
    completeLogin(new Response(null, { status: 204, headers: { 'X-Robopark-Session': token } }))
    await stale
    expect(sessionStorage.getItem(key)).toBeNull()
  })

  it.each([401, 403, 503])('does not retain capability after failed login (%s)', async status => {
    sessionStorage.setItem(key, token)
    vi.stubGlobal('fetch', vi.fn(async () => json({}, status)))
    await initializeDesktopTransport()
    await fetch('/api/auth/login', { method: 'POST' })
    expect(sessionStorage.getItem(key)).toBeNull()
  })

  it('clears stale capabilities on expired launch and still renders the public login flow', async () => {
    sessionStorage.setItem(key, token)
    vi.stubGlobal('location', new URL(`${origin}/#rp-launch=${nonce}`))
    vi.spyOn(window.history, 'replaceState').mockImplementation(() => undefined)
    const native = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => json({}, 401))
    vi.stubGlobal('fetch', native)
    await initializeDesktopTransport()
    expect(sessionStorage.getItem(key)).toBeNull()
    await fetch('/api/auth/login', { method: 'POST' })
    expect(new Headers(native.mock.calls[1][1]?.headers).has('X-Robopark-Session')).toBe(false)
  })

  it('leaves ordinary web and PWA fetch untouched', async () => {
    setDesktopRuntimeForTests(null)
    const native = vi.fn()
    vi.stubGlobal('fetch', native)
    await initializeDesktopTransport()
    expect(fetch).toBe(native)
  })

  it('distinguishes a stale local tab from an upstream revoked account without replaying mutations', async () => {
    sessionStorage.setItem(key, token)
    const native = vi.fn(async () => json({ detail: 'local_session_required' }, 401))
    vi.stubGlobal('fetch', native)
    await initializeDesktopTransport()
    await expect(fetch('/api/tasks', { method: 'POST', body: '{}' })).rejects.toBeInstanceOf(DesktopSessionRequiredError)
    expect(native).toHaveBeenCalledOnce()
    expect(sessionStorage.getItem(key)).toBeNull()
    native.mockImplementation(async () => json({ detail: 'not_authenticated' }, 401))
    expect((await fetch('/api/auth/me')).status).toBe(401)
  })

  it('recognizes stale OTA HEAD sessions without a response body', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, {
      status: 401, headers: { 'X-Robopark-Local-Session': 'required' },
    })))
    await initializeDesktopTransport()
    await expect(fetch('/api/admin/ota/uploads/id', { method: 'HEAD' })).rejects.toBeInstanceOf(DesktopSessionRequiredError)
  })
})
