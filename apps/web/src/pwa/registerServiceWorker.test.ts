import { describe, expect, it, vi } from 'vitest'
import { registerServiceWorker } from './registerServiceWorker'

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
})
