import { afterEach, describe, expect, it, vi } from 'vitest'
import { nativeTelegramClient } from './nativeTelegramApi'

afterEach(() => vi.restoreAllMocks())

describe('nativeTelegramClient', () => {
  it('uses the native admin routes and preserves UUID job ids and revisions', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ parks: [], jobs: [], deliveries: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await nativeTelegramClient.getAdmin()
    await nativeTelegramClient.deleteJob('17fe4e2a-93e9-4a62-8d6a-806b9fcb0914', 7)

    expect(fetchMock.mock.calls[0][0]).toBe('/api/admin/bot/native')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/admin/bot/native/jobs/17fe4e2a-93e9-4a62-8d6a-806b9fcb0914?revision=7')
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'DELETE', credentials: 'include' })
  })

  it('uses the current-user account routes without accepting another user id', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ linked: false, telegram_user_id: null }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ code: 'ABCD12', expires_at: '2026-10-07T10:00:00Z' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    await nativeTelegramClient.getAccount()
    await nativeTelegramClient.createLinkCode()
    await nativeTelegramClient.unlinkAccount()

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/bot/account', '/api/bot/account/link-code', '/api/bot/account',
    ])
  })

  it('uses the royal migration preview and fingerprint apply routes', async () => {
    const response = { fingerprint: 'a'.repeat(64), already_applied: false, park_updates: [], jobs: [], conflicts: [], counts: { park_updates: 0, jobs: 0, conflicts: 0, skipped: 0 } }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(response), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...response, applied: true, applied_at: '2026-10-07T12:00:00Z' }), { status: 200 }))

    await nativeTelegramClient.getMigrationPreview()
    await nativeTelegramClient.applyMigration(response.fingerprint)

    expect(fetchMock.mock.calls[0][0]).toBe('/api/admin/bot/native/migration/preview')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/admin/bot/native/migration/apply')
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: 'POST', body: JSON.stringify({ fingerprint: response.fingerprint }) })
  })
})
