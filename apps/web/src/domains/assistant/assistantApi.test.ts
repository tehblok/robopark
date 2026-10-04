import { afterEach, describe, expect, it, vi } from 'vitest'
import { assistantApi, parseKnowledgeFile } from './assistantApi'

afterEach(() => vi.unstubAllGlobals())

describe('assistantApi', () => {
  it('sends a chat message with the idempotency key required by the durable job contract', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 'job-1', kind: 'chat', state: 'queued', created_at: '2026-10-04T10:00:00Z',
      updated_at: '2026-10-04T10:00:00Z', error: null, result: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)

    await assistantApi.sendMessage('conversation-7', 'Как проверить лидар?', 'message-9')

    expect(fetch).toHaveBeenCalledWith('/api/ai/conversations/conversation-7/messages', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ content: 'Как проверить лидар?', idempotency_key: 'message-9' }),
    }))
  })

  it('builds document search without leaking undefined filters', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, offset: 0, limit: 30 }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetch)

    await assistantApi.documents({ q: 'тормоз', park_id: 4 })

    expect(fetch).toHaveBeenCalledWith('/api/ai/documents?q=%D1%82%D0%BE%D1%80%D0%BC%D0%BE%D0%B7&park_id=4&offset=0&limit=30', expect.anything())
  })
})

describe('parseKnowledgeFile', () => {
  it('reads JSONL, reports malformed rows, and keeps valid documents for batch import', async () => {
    const file = new File([
      '{"title":"Лидар","content":"Проверить разъём","kind":"manual","source_ref":"kb-1"}\n',
      'not-json\n',
      '{"title":"","content":"missing title","kind":"note","source_ref":"kb-2"}\n',
      '{"title":"Тормоз","content":"Осмотр колодок","kind":"note","source_ref":"kb-3"}\n',
    ], 'knowledge.jsonl', { type: 'application/x-ndjson' })

    const result = await parseKnowledgeFile(file)

    expect(result.documents).toEqual([
      { title: 'Лидар', content: 'Проверить разъём', kind: 'manual', source_ref: 'kb-1' },
      { title: 'Тормоз', content: 'Осмотр колодок', kind: 'note', source_ref: 'kb-3' },
    ])
    expect(result.rejected).toBe(2)
  })
})
