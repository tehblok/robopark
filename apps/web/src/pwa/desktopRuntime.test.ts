import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { desktopRuntime, getInstallationDeviceId, isDesktopLoopbackOrigin, readProtectedLocalValue, removeProtectedLocalValue, resetDesktopStorageKeyForTests, sealDesktopJson, setDesktopRuntimeForTests, unsealDesktopJson, writeProtectedLocalValue } from './desktopRuntime'

describe('desktop runtime', () => {
  beforeEach(() => {
    localStorage.clear()
    delete window.__ROBOPARK_DESKTOP__
    resetDesktopStorageKeyForTests()
    setDesktopRuntimeForTests(undefined)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('uses the native installation identity instead of an account-derived sync id', () => {
    window.__ROBOPARK_DESKTOP__ = { version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' }
    const first = getInstallationDeviceId(localStorage, { location: new URL('http://127.0.0.1:18444/') })
    const second = getInstallationDeviceId(localStorage, { location: new URL('http://127.0.0.1:18444/') })
    expect(first).toBe(second)
    expect(first).toMatch(/^mac-[0-9a-f-]{36}$/)
    expect(first).not.toContain('6f17d720-9fab-48da-8dd5-9f3d270b2878')
  })

  it('does not reuse a Mac installation id after the server deployment changes', () => {
    Object.defineProperty(window, '__ROBOPARK_DESKTOP__', { configurable: true, writable: true, value: { version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' } })
    const first = getInstallationDeviceId(localStorage, { location: new URL('http://127.0.0.1:18444/') })
    window.__ROBOPARK_DESKTOP__ = { version: 1, instanceId: '794f1414-c405-4cee-95e2-85291db193c0' }
    expect(getInstallationDeviceId(localStorage, { location: new URL('http://127.0.0.1:18444/') })).not.toBe(first)
  })

  it('keeps one random browser installation id across accounts and reloads', () => {
    const first = getInstallationDeviceId()
    const second = getInstallationDeviceId()
    expect(first).toBe(second)
    expect(first).toMatch(/^web-[0-9a-f-]{36}$/)
    expect(first).not.toContain('account')
  })

  it('rejects malformed or non-loopback desktop bootstrap data', () => {
    window.__ROBOPARK_DESKTOP__ = { version: 1, instanceId: 'not-a-uuid' }
    expect(desktopRuntime({ location: new URL('http://127.0.0.1:18444/') })).toBeNull()
    window.__ROBOPARK_DESKTOP__ = { version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' }
    expect(desktopRuntime({ location: new URL('https://example.test/') })).toBeNull()
    expect(isDesktopLoopbackOrigin({ location: new URL('http://127.0.0.1:18444/terminal.html') })).toBe(true)
  })

  it('encrypts desktop persisted JSON with an in-memory native AES key and fails closed without it', async () => {
    window.__ROBOPARK_DESKTOP__ = { version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' }
    const bytes = crypto.getRandomValues(new Uint8Array(32))
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ key: btoa(String.fromCharCode(...bytes)) }), {
      status: 200, headers: { 'content-type': 'application/json' },
    })))

    const sealed = await sealDesktopJson({ secret: 'offline' }, { location: new URL('http://127.0.0.1:18444/') })
    expect(JSON.stringify(sealed)).not.toContain('offline')
    expect(await unsealDesktopJson(sealed, { location: new URL('http://127.0.0.1:18444/') })).toEqual({ secret: 'offline' })
    expect(fetch).toHaveBeenCalledWith('/__robopark/storage-key', expect.objectContaining({ cache: 'no-store', credentials: 'same-origin' }))

    resetDesktopStorageKeyForTests()
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 403 })))
    await expect(unsealDesktopJson(sealed, { location: new URL('http://127.0.0.1:18444/') })).rejects.toThrow('desktop_storage_key_unavailable')
  })

  it('persists desktop text drafts encrypted and prevents a pending save from resurrecting a removed draft', async () => {
    setDesktopRuntimeForTests({ version: 1, instanceId: '6f17d720-9fab-48da-8dd5-9f3d270b2878' })
    const bytes = crypto.getRandomValues(new Uint8Array(32))
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ key: btoa(String.fromCharCode(...bytes)) }), { status: 200 })))
    await writeProtectedLocalValue('draft', 'sensitive text')
    expect(localStorage.getItem('draft')).not.toContain('sensitive text')
    expect(await readProtectedLocalValue('draft')).toBe('sensitive text')
    localStorage.setItem('another-account-or-task', localStorage.getItem('draft')!)
    expect(await readProtectedLocalValue('another-account-or-task')).toBeNull()

    const pending = writeProtectedLocalValue('draft', 'must stay deleted')
    removeProtectedLocalValue('draft')
    await pending
    expect(localStorage.getItem('draft')).toBeNull()
  })
})
