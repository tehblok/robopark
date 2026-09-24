import { describe, expect, it, vi } from 'vitest'
import { activateServiceWorkerWhenSafe, registerServiceWorker, setServiceWorkerSyncState } from './registerServiceWorker'

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

  it('reports the current fallback queue to a waiting worker checking all open clients', async () => {
    const listeners = new Map<string, (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void }[] }) => void>()
    const replies: unknown[] = []
    await registerServiceWorker({
      production: true, secure: true,
      serviceWorker: { register: vi.fn(async () => ({ update: vi.fn(async () => undefined) })), addEventListener: (name, listener) => listeners.set(name, listener) },
    })
    setServiceWorkerSyncState({ status: 'attention', pending: 0, conflicts: 1 })
    listeners.get('message')?.({ data: { type: 'CHECK_ACTIVATION_SAFETY' }, ports: [{ postMessage: value => replies.push(value) }] })
    expect(replies).toEqual([{ safe: false }])
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 })
    listeners.get('message')?.({ data: { type: 'CHECK_ACTIVATION_SAFETY' }, ports: [{ postMessage: value => replies.push(value) }] })
    expect(replies.at(-1)).toEqual({ safe: true })
    setServiceWorkerSyncState(null)
  })
})
