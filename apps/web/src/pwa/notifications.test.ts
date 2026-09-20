import { describe, expect, it, vi } from 'vitest'
import { decodeApplicationServerKey, enableSystemNotifications } from './notifications'

describe('enableSystemNotifications', () => {
  it('decodes the VAPID public key', () => {
    expect([...decodeApplicationServerKey('AQIDBA')]).toEqual([1, 2, 3, 4])
  })
  it('asks browser permission only when explicitly invoked', async () => {
    const requestPermission = vi.fn(async () => 'granted' as NotificationPermission)
    vi.stubGlobal('Notification', { permission: 'default', requestPermission })
    const subscribe = vi.fn(async () => ({ endpoint: 'https://push.test/1', getKey: () => new Uint8Array([1]).buffer }))
    const registration = { pushManager: { subscribe } } as unknown as ServiceWorkerRegistration

    expect(requestPermission).not.toHaveBeenCalled()
    await enableSystemNotifications(registration, new Uint8Array([2]))

    expect(requestPermission).toHaveBeenCalledOnce()
    expect(subscribe).toHaveBeenCalledOnce()
  })

  it('keeps the internal inbox usable when permission is denied', async () => {
    vi.stubGlobal('Notification', { permission: 'default', requestPermission: vi.fn(async () => 'denied') })
    const registration = { pushManager: { subscribe: vi.fn() } } as unknown as ServiceWorkerRegistration
    await expect(enableSystemNotifications(registration, new Uint8Array([2]))).resolves.toEqual({ status: 'denied' })
  })
})
