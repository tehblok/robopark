import { describe, expect, it, vi } from 'vitest'
import { activateServiceWorkerWhenSafe, registerServiceWorker, runLocalWork, setServiceWorkerAuthState, setServiceWorkerSyncState } from './registerServiceWorker'

describe('registerServiceWorker', () => {
  it('registers only in secure production and checks for updates when returning', async () => {
    const update = vi.fn().mockResolvedValue(undefined)
    const register = vi.fn().mockResolvedValue({ update })
    let focus: (() => void) | undefined
    const ok = await registerServiceWorker({
      production: true, secure: true, serviceWorker: { register },
      onFocus: (callback) => { focus = callback },
    })
    expect(ok).toBe(true)
    expect(register).toHaveBeenCalledWith('/sw.js', { scope: '/' })
    focus?.()
    expect(update).toHaveBeenCalledTimes(1)
  })

  it('does not register in development or without a secure browser worker', async () => {
    const register = vi.fn()
    expect(await registerServiceWorker({ production: false, secure: true, serviceWorker: { register } })).toBe(false)
    expect(await registerServiceWorker({ production: true, secure: false, serviceWorker: { register } })).toBe(false)
    expect(await registerServiceWorker({ production: true, secure: true })).toBe(false)
    expect(register).not.toHaveBeenCalled()
  })

  it('does not interrupt the site when registration fails', async () => {
    const register = vi.fn().mockRejectedValue(new Error('blocked'))
    expect(await registerServiceWorker({ production: true, secure: true, serviceWorker: { register } })).toBe(false)
  })

  it('keeps the waiting worker while local work is pending and activates it explicitly when safe', () => {
    const postMessage = vi.fn()
    const registration = { waiting: { postMessage } }
    expect(activateServiceWorkerWhenSafe(registration, { status: 'idle', pending: 1, conflicts: 0 })).toBe(false)
    expect(activateServiceWorkerWhenSafe(registration, { status: 'syncing', pending: 0, conflicts: 0 })).toBe(false)
    expect(activateServiceWorkerWhenSafe(registration, { status: 'attention', pending: 0, conflicts: 0 })).toBe(false)
    expect(activateServiceWorkerWhenSafe(registration, { status: 'idle', pending: 0, conflicts: 1 })).toBe(false)
    expect(postMessage).not.toHaveBeenCalled()

    expect(activateServiceWorkerWhenSafe(registration, { status: 'idle', pending: 0, conflicts: 0 })).toBe(true)
    expect(postMessage).toHaveBeenCalledWith({ type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } })
  })

  it('reloads an open client after the explicitly activated worker takes control', async () => {
    const listeners = new Map<string, (event: { data?: unknown }) => void>()
    const reload = vi.fn()
    const registration = { update: vi.fn(async () => undefined), waiting: { postMessage: vi.fn() } }
    await registerServiceWorker({
      production: true, secure: true, reload,
      serviceWorker: { register: vi.fn(async () => registration), addEventListener: (name, listener) => listeners.set(name, listener) },
    })
    expect(activateServiceWorkerWhenSafe(registration, { status: 'idle', pending: 0, conflicts: 0 })).toBe(true)
    listeners.get('controllerchange')?.({})
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('keeps startup, delayed authentication, and a second tab unknown until each client reports a safe state', async () => {
    const listeners = new Map<string, (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void }[] }) => void>()
    const replies: unknown[] = []
    await registerServiceWorker({
      production: true, secure: true,
      serviceWorker: { register: vi.fn(async () => ({ update: vi.fn(async () => undefined) })), addEventListener: (name, listener) => listeners.set(name, listener) },
    })
    const ask = () => {
      const port = { postMessage: (value: unknown) => replies.push(value), onmessage: undefined as undefined | ((event: { data?: unknown }) => void) }
      listeners.get('message')?.({ data: { type: 'PREPARE_ACTIVATION' }, ports: [port] })
      return port
    }
    setServiceWorkerAuthState({ loading: true, accountId: null })
    ask()
    expect(replies.at(-1)).toEqual({ safe: false })
    setServiceWorkerAuthState({ loading: false, accountId: null })
    const anonymous = ask()
    expect(replies.at(-1)).toEqual({ safe: true })
    anonymous.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    ask()
    expect(replies.at(-1)).toEqual({ safe: false })
    setServiceWorkerSyncState({ status: 'attention', pending: 0, conflicts: 1 }, 7)
    ask()
    expect(replies.at(-1)).toEqual({ safe: false })
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 }, 7)
    const prepared = ask()
    expect(replies.at(-1)).toEqual({ safe: true })
    setServiceWorkerSyncState({ status: 'attention', pending: 0, conflicts: 1 }, 7)
    expect(replies.at(-1)).toEqual({ type: 'VETO_ACTIVATION' })
    prepared.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    setServiceWorkerAuthState({ loading: true, accountId: null })
    setServiceWorkerSyncState(null)
  })

  it('blocks a fallback enqueue synchronously during the activation fence', async () => {
    const listeners = new Map<string, (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void, onmessage?: (event: { data?: unknown }) => void }[] }) => void>()
    await registerServiceWorker({
      production: true, secure: true,
      serviceWorker: { register: vi.fn(async () => ({ update: vi.fn(async () => undefined) })), addEventListener: (name, listener) => listeners.set(name, listener) },
    })
    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 }, 7)
    const port = { postMessage: vi.fn(), onmessage: undefined as undefined | ((event: { data?: unknown }) => void) }
    listeners.get('message')?.({ data: { type: 'PREPARE_ACTIVATION' }, ports: [port] })
    expect(port.postMessage).toHaveBeenCalledWith({ safe: true })
    const enqueue = vi.fn(async () => 'accepted')
    await expect(runLocalWork(enqueue)).rejects.toThrow('pwa_update_in_progress')
    expect(enqueue).not.toHaveBeenCalled()
    port.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    await expect(runLocalWork(enqueue)).resolves.toBe('accepted')
    setServiceWorkerAuthState({ loading: true, accountId: null })
    setServiceWorkerSyncState(null)
  })
})
