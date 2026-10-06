import { afterEach, describe, expect, it, vi } from 'vitest'
import { assistantApi } from './assistantApi'

afterEach(() => vi.unstubAllGlobals())

describe('assistantApi', () => {
  it('forwards cancellation to status and job reads', async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({}), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetch)
    const controller = new AbortController()

    await assistantApi.status(controller.signal)
    await assistantApi.job('job-1', controller.signal)

    expect(fetch).toHaveBeenNthCalledWith(1, '/api/ai/status', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(fetch).toHaveBeenNthCalledWith(2, '/api/ai/jobs/job-1', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('sends a chat message with the idempotency key required by the durable job contract', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'job-1', kind: 'chat', state: 'queued', created_at: '2026-10-04T10:00:00Z',
      updated_at: '2026-10-04T10:00:00Z', error: null, result: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)

    await assistantApi.sendMessage('conversation-7', 'Как проверить лидар?', 'message-9')

    expect(fetch).toHaveBeenCalledWith('/api/ai/conversations/conversation-7/messages', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ content: 'Как проверить лидар?', idempotency_key: 'message-9', use_tools: true }),
    }))
  })

})
