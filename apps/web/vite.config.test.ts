// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest'
import type { UserConfig } from 'vite'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

it('routes the isolated demo through its own API port', async () => {
  vi.stubEnv('ROBOPARK_DEV_API_TARGET', 'http://127.0.0.1:49123')
  vi.resetModules()
  const config = (await import('./vite.config')).default as UserConfig
  const proxy = config.server?.proxy?.['/api']
  expect(typeof proxy).toBe('object')
  if (typeof proxy === 'object') expect(proxy.target).toBe('http://127.0.0.1:49123')
})

it('rejects a remote API target in local development', async () => {
  vi.stubEnv('ROBOPARK_DEV_API_TARGET', 'https://example.invalid')
  vi.resetModules()
  await expect(import('./vite.config')).rejects.toThrow('ROBOPARK_DEV_API_TARGET must be a loopback HTTP URL')
})

it('proxies terminal websocket upgrades and builds the isolated HTML entry', async () => {
  const config = (await import('./vite.config')).default as UserConfig
  const proxy = config.server?.proxy?.['/api']
  expect(typeof proxy).toBe('object')
  if (typeof proxy === 'object') expect(proxy.ws).toBe(true)
  expect(config.build?.rollupOptions?.input).toMatchObject({ app: expect.stringMatching(/index\.html$/), terminal: expect.stringMatching(/terminal\.html$/) })
})
