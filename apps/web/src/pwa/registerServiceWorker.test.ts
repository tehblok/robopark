import { afterEach, describe, expect, it, vi } from 'vitest'
import { activateServiceWorkerWhenSafe, registerServiceWorker, runLocalWork, setServiceWorkerAuthState, setServiceWorkerSyncState } from './registerServiceWorker'

describe('registerServiceWorker', () => {
  afterEach(() => {
    setServiceWorkerAuthState(null)
    setServiceWorkerSyncState(null)
  })
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

    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 }, 7)
    expect(activateServiceWorkerWhenSafe(registration, { status: 'idle', pending: 0, conflicts: 0 })).toBe(true)
    expect(postMessage).toHaveBeenCalledWith({ type: 'ACTIVATE_WHEN_SAFE', state: { status: 'idle', pending: 0, conflicts: 0 } })
  })

  it('does not manually activate an update during a local write even if the last sync snapshot is idle', async () => {
    const postMessage = vi.fn()
    const registration = { waiting: { postMessage } }
    const idle = { status: 'idle' as const, pending: 0, conflicts: 0 }
    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    setServiceWorkerSyncState(idle, 7)
    let finish!: () => void
    const localWrite = runLocalWork(() => new Promise<void>(resolve => { finish = resolve }))

    expect(activateServiceWorkerWhenSafe(registration, idle)).toBe(false)
    expect(postMessage).not.toHaveBeenCalled()
    finish()
    await localWrite
    expect(activateServiceWorkerWhenSafe(registration, idle)).toBe(true)
  })

  it('requests a waiting update after the last local write finishes', async () => {
    const postMessage = vi.fn()
    const registration = { update: vi.fn(async () => undefined), waiting: { postMessage } }
    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 }, 7)
    let finish!: () => void
    const localWrite = runLocalWork(() => new Promise<void>(resolve => { finish = resolve }))
    await registerServiceWorker({ production: true, secure: true, serviceWorker: { register: vi.fn(async () => registration) } })
    expect(postMessage).not.toHaveBeenCalled()

    finish()
    await localWrite
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
    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 }, 7)
    expect(activateServiceWorkerWhenSafe(registration, { status: 'idle', pending: 0, conflicts: 0 })).toBe(true)
    listeners.get('controllerchange')?.({})
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('reloads after a waiting worker initiated a safe activation handshake', async () => {
    const listeners = new Map<string, (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void, onmessage?: (event: { data?: unknown }) => void }[] }) => void>()
    const reload = vi.fn()
    await registerServiceWorker({
      production: true, secure: true, reload,
      serviceWorker: { register: vi.fn(async () => ({ update: vi.fn(async () => undefined) })), addEventListener: (name, listener) => listeners.set(name, listener) },
    })
    setServiceWorkerAuthState({ loading: false, accountId: null })
    const port = { postMessage: vi.fn(), onmessage: undefined as undefined | ((event: { data?: unknown }) => void) }

    listeners.get('message')?.({ data: { type: 'PREPARE_ACTIVATION' }, ports: [port] })
    expect(port.postMessage).toHaveBeenCalledWith({ safe: true, reloadOnControllerChange: true })
    listeners.get('controllerchange')?.({})

    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('waits for the controlling worker to finish activation before reloading its document', async () => {
    const listeners = new Map<string, (event: { data?: unknown }) => void>()
    const stateListeners = new Map<string, () => void>()
    const reload = vi.fn()
    const controller = { state: 'activating', addEventListener: (name: string, listener: () => void) => stateListeners.set(name, listener) }
    const registration = { update: vi.fn(async () => undefined), waiting: { postMessage: vi.fn() } }
    const serviceWorker = { controller, register: vi.fn(async () => registration), addEventListener: (name: string, listener: (event: { data?: unknown }) => void) => listeners.set(name, listener) }
    setServiceWorkerAuthState({ loading: false, accountId: null })
    await registerServiceWorker({ production: true, secure: true, reload, serviceWorker })
    listeners.get('controllerchange')?.({})
    expect(reload).not.toHaveBeenCalled()
    controller.state = 'activated'
    stateListeners.get('statechange')?.()
    expect(reload).toHaveBeenCalledTimes(1)
    stateListeners.get('statechange')?.()
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('keeps local writes fenced until the new controller is activated', async () => {
    const listeners = new Map<string, (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void }[] }) => void>()
    const stateListeners = new Map<string, () => void>()
    const controller = { state: 'activating', addEventListener: (name: string, listener: () => void) => stateListeners.set(name, listener) }
    setServiceWorkerAuthState({ loading: false, accountId: null })
    await registerServiceWorker({ production: true, secure: true, serviceWorker: {
      controller, register: vi.fn(async () => ({ update: vi.fn(async () => undefined) })),
      addEventListener: (name, listener) => listeners.set(name, listener),
    } })
    listeners.get('message')?.({ data: { type: 'PREPARE_ACTIVATION' }, ports: [{ postMessage: vi.fn() }] })
    listeners.get('controllerchange')?.({})
    await expect(runLocalWork(async () => 'new draft')).rejects.toThrow('pwa_update_in_progress')
    controller.state = 'activated'
    stateListeners.get('statechange')?.()
    await expect(runLocalWork(async () => 'new draft')).resolves.toBe('new draft')
  })

  it('activates a waiting update automatically once the authenticated client is safe', async () => {
    const postMessage = vi.fn()
    const registration = { update: vi.fn(async () => undefined), waiting: { postMessage } }
    await registerServiceWorker({
      production: true, secure: true,
      serviceWorker: { register: vi.fn(async () => registration) },
    })

    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    expect(postMessage).not.toHaveBeenCalled()
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 }, 7)

    expect(postMessage).toHaveBeenCalledWith({
      type: 'ACTIVATE_WHEN_SAFE',
      state: { status: 'idle', pending: 0, conflicts: 0 },
    })
    setServiceWorkerAuthState({ loading: true, accountId: null })
    setServiceWorkerSyncState(null)
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
    expect(replies.at(-1)).toEqual({ safe: false, reloadOnControllerChange: true })
    setServiceWorkerAuthState({ loading: false, accountId: null })
    const anonymous = ask()
    expect(replies.at(-1)).toEqual({ safe: true, reloadOnControllerChange: true })
    anonymous.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    setServiceWorkerAuthState({ loading: false, accountId: 7 })
    ask()
    expect(replies.at(-1)).toEqual({ safe: false, reloadOnControllerChange: true })
    setServiceWorkerSyncState({ status: 'attention', pending: 0, conflicts: 1 }, 7)
    ask()
    expect(replies.at(-1)).toEqual({ safe: false, reloadOnControllerChange: true })
    setServiceWorkerSyncState({ status: 'idle', pending: 0, conflicts: 0 }, 7)
    const prepared = ask()
    expect(replies.at(-1)).toEqual({ safe: true, reloadOnControllerChange: true })
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
    expect(port.postMessage).toHaveBeenCalledWith({ safe: true, reloadOnControllerChange: true })
    const enqueue = vi.fn(async () => 'accepted')
    await expect(runLocalWork(enqueue)).rejects.toThrow('pwa_update_in_progress')
    expect(enqueue).not.toHaveBeenCalled()
    port.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    await expect(runLocalWork(enqueue)).resolves.toBe('accepted')
    setServiceWorkerAuthState({ loading: true, accountId: null })
    setServiceWorkerSyncState(null)
  })
})

describe('failed worker activation recovery', () => {
  afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })

  async function preparedClient(controlled: boolean) {
    vi.resetModules()
    vi.useFakeTimers()
    const api = await import('./registerServiceWorker')
    const stateListeners = new Set<() => void>()
    const worker = {
      state: 'activating',
      addEventListener: (_name: string, callback: () => void) => { stateListeners.add(callback) },
      removeEventListener: (_name: string, callback: () => void) => { stateListeners.delete(callback) },
    }
    type Message = { data?: unknown, source?: typeof worker, ports?: { postMessage: (value: unknown) => void }[] }
    const listeners = new Map<string, (event: Message) => void>()
    const serviceWorker = {
      controller: controlled ? worker : null,
      register: vi.fn(async () => ({ update: vi.fn(async () => undefined) })),
      addEventListener: (name: string, callback: (event: Message) => void) => { listeners.set(name, callback) },
    }
    const reload = vi.fn()
    await api.registerServiceWorker({ production: true, secure: true, serviceWorker, reload })
    api.setServiceWorkerAuthState({ loading: false, accountId: null })
    const port = { postMessage: vi.fn(), onmessage: undefined as undefined | ((event: { data?: unknown }) => void) }
    listeners.get('message')?.({ data: { type: 'PREPARE_ACTIVATION' }, source: worker, ports: [port] })
    if (controlled) listeners.get('controllerchange')?.({})
    await expect(api.runLocalWork(async () => 'draft')).rejects.toThrow('pwa_update_in_progress')
    return { api, worker, stateListeners, serviceWorker, listeners, reload, port }
  }

  it.each([false, true])('releases local writes when the activating worker becomes redundant (controlled=%s)', async controlled => {
    const client = await preparedClient(controlled)
    client.worker.state = 'redundant'
    for (const listener of [...client.stateListeners]) listener()
    await expect(client.api.runLocalWork(async () => 'saved')).resolves.toBe('saved')
    expect(client.reload).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('expires a lost handshake and does not reload a late controller during new local work', async () => {
    const client = await preparedClient(false)
    await vi.advanceTimersByTimeAsync(60_001)
    let finish!: () => void
    const work = client.api.runLocalWork(() => new Promise<void>(resolve => { finish = resolve }))
    await Promise.resolve()
    expect(finish).toBeTypeOf('function')
    expect(client.port.postMessage).toHaveBeenCalledWith({ type: 'VETO_ACTIVATION' })
    client.worker.state = 'activated'
    client.serviceWorker.controller = client.worker
    client.listeners.get('controllerchange')?.({})
    expect(client.reload).not.toHaveBeenCalled()
    finish()
    await work
  })

  it('does not reload new local work after the worker explicitly aborts its handshake', async () => {
    const client = await preparedClient(false)
    client.port.onmessage?.({ data: { type: 'RELEASE_ACTIVATION' } })
    expect(client.port.postMessage).toHaveBeenCalledWith({ type: 'RELEASED_ACTIVATION' })
    let finish!: () => void
    const work = client.api.runLocalWork(() => new Promise<void>(resolve => { finish = resolve }))
    client.worker.state = 'activated'
    client.serviceWorker.controller = client.worker
    client.listeners.get('controllerchange')?.({})
    expect(client.reload).not.toHaveBeenCalled()
    finish()
    await work
    expect(vi.getTimerCount()).toBe(0)
  })
})
