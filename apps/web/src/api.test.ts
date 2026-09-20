import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiTimeoutError, api, clearApiValidators } from './api'

type ApiCall = () => Promise<unknown>

const requestIdCases: Array<[string, ApiCall]> = [
  ['JSON', () => api.trackerIssue('ROBOPARK-42')],
  ['blob', () => api.exportEmergencyConfig()],
  [
    'form',
    () => api.trackerAttach('ROBOPARK-42', new File(['image'], 'robot.png', { type: 'image/png' })),
  ],
]

const timeoutCases: Array<[string, number, ApiCall]> = [
  ['JSON', 30_000, () => api.trackerIssue('ROBOPARK-42')],
  ['blob', 60_000, () => api.exportEmergencyConfig()],
  [
    'form',
    90_000,
    () => api.trackerAttach('ROBOPARK-42', new File(['image'], 'robot.png', { type: 'image/png' })),
  ],
]

const bodyTimeoutCases: Array<[string, number, number, ApiCall]> = [
  ['successful JSON body', 30_000, 200, () => api.trackerIssue('ROBOPARK-42')],
  ['error JSON body', 30_000, 502, () => api.trackerIssue('ROBOPARK-42')],
  ['blob body', 60_000, 200, () => api.exportEmergencyConfig()],
  [
    'form response body',
    90_000,
    200,
    () => api.trackerAttach('ROBOPARK-42', new File(['image'], 'robot.png', { type: 'image/png' })),
  ],
]

function stalledBodyResponse(
  signal: AbortSignal | null | undefined,
  status: number,
  onBodyRead?: () => void,
): Response {
  const body = new ReadableStream({
    type: 'bytes',
    start(controller) {
      const abort = () => controller.error(
        signal?.reason ?? new DOMException('Aborted', 'AbortError'),
      )
      if (signal?.aborted) abort()
      else signal?.addEventListener('abort', abort, { once: true })
    },
    pull() {
      onBodyRead?.()
      return new Promise<void>(() => undefined)
    },
  } as UnderlyingByteSource)
  return new Response(body, {
    status,
    headers: {
      'Content-Type': 'application/json',
      'X-Request-ID': 'req-body',
    },
  })
}

describe('API transport metadata', () => {
  afterEach(() => {
    clearApiValidators()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('reuses the typed tracker payload on ETag 304', async () => {
    const payload = { items: [{ key: 'ROBOPARK-42' }], total: 1, limit: 50, offset: 0, has_more: false }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json', ETag: '"issues-v1"' },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 304 }))
    vi.stubGlobal('fetch', fetchMock)

    expect(await api.trackerIssues({ limit: 50 })).toEqual(payload)
    expect(await api.trackerIssues({ limit: 50 })).toEqual(payload)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/tracker/issues?sort=oldest&limit=50',
      expect.objectContaining({ headers: { 'If-None-Match': '"issues-v1"' } }))
  })

  it('does not retain a tracker validator completed after authorization changed', async () => {
    let complete!: (response: Response) => void
    const firstResponse = new Promise<Response>(resolve => { complete = resolve })
    const payload = { items: [], total: 0, limit: 50, offset: 0, has_more: false }
    const fetchMock = vi.fn()
      .mockReturnValueOnce(firstResponse)
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json', ETag: '"new-account"' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    const oldAccountRequest = api.trackerIssues({ limit: 50 })
    clearApiValidators()
    complete(new Response(JSON.stringify(payload), {
      status: 200, headers: { 'Content-Type': 'application/json', ETag: '"old-account"' },
    }))
    await oldAccountRequest
    await api.trackerIssues({ limit: 50 })

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/tracker/issues?sort=oldest&limit=50',
      expect.objectContaining({ headers: {} }))
  })

  it.each(requestIdCases)('copies X-Request-ID into an ApiError for a %s request', async (_label, call) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: 'tracker_upstream_error' }), {
          status: 502,
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': 'req-42',
          },
        }),
      ),
    )

    await expect(call()).rejects.toMatchObject({
      status: 502,
      detail: 'tracker_upstream_error',
      requestId: 'req-42',
    })
  })

  it('sends the deterministic oldest sort when work filters omit it', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ items: [], total: 0, limit: 50, offset: 0, has_more: false }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.trackerIssues({ limit: 50, offset: 0 })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tracker/issues?sort=oldest&limit=50&offset=0',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('requests locally owned work without Tracker assignee or park filters', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ items: [], total: 0, limit: 50, offset: 0, has_more: false }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.trackerIssues({ owned_by_me: true, open_only: true, limit: 50, offset: 0 })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tracker/issues?sort=oldest&owned_by_me=true&open_only=true&limit=50&offset=0',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('sends manager task recovery and hide controls to named idempotent routes', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({
      key: 'ROBOPARK-42', action: 'ok', status: 'saved', actor: 'admin',
      performed_at: '2026-09-15T09:00:00Z', sync_state: 'pending', workflow: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await api.taskRetryNow('ROBOPARK-42', 'retry-key')
    await api.taskHide('ROBOPARK-42', 'Дубль', 'hide-key')
    await api.taskRestore('ROBOPARK-42', 'restore-key')

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/tracker/issues/ROBOPARK-42/retry-now', expect.objectContaining({ method: 'POST', headers: expect.objectContaining({ 'Idempotency-Key': 'retry-key' }) }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/tracker/issues/ROBOPARK-42/hide', expect.objectContaining({ method: 'POST', body: JSON.stringify({ reason: 'Дубль' }) }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/tracker/issues/ROBOPARK-42/hide', expect.objectContaining({ method: 'DELETE', headers: expect.objectContaining({ 'Idempotency-Key': 'restore-key' }) }))
  })

  it('keeps exact related-robot matching separate from the generic summary search', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({ items: [], total: 0, limit: 10, offset: 0, has_more: false }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.trackerIssues({
      queue: 'ROBOPARK',
      park: 'Alpha',
      robot_exact: '447',
      exclude_key: 'ROBOPARK-42',
      limit: 10,
      offset: 0,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/tracker/issues?sort=oldest&queue=ROBOPARK&park=Alpha&robot_exact=447&exclude_key=ROBOPARK-42&limit=10&offset=0',
      expect.objectContaining({ credentials: 'include' }),
    )
  })

  it('uploads a report attachment to an existing report instead of creating another report', async () => {
    let submitted: FormData | undefined
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      submitted = init?.body as FormData | undefined
      return new Response(JSON.stringify({
        id: 11,
        kind: 'device_photo',
        filename: 'robot.jpg',
        content_type: 'image/jpeg',
        size_bytes: 3,
      }), { status: 201, headers: { 'Content-Type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchMock)

    await api.reportAttach(42, 'device_photo', new File(['jpg'], 'robot.jpg', {
      type: 'image/jpeg',
    }))

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/reports/42/attachments',
      expect.objectContaining({ credentials: 'include', method: 'POST' }),
    )
    expect(submitted).toBeInstanceOf(FormData)
    expect(submitted?.get('kind')).toBe('device_photo')
  })

  it.each(timeoutCases)(
    'cancels a stalled %s request at the %dms transport deadline',
    async (_label, timeoutMs, call) => {
      vi.useFakeTimers()
      const observed: { signal?: AbortSignal } = {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            observed.signal = init?.signal ?? undefined
            observed.signal?.addEventListener(
              'abort',
              () => reject(new DOMException('Aborted', 'AbortError')),
              { once: true },
            )
          }),
        ),
      )

      const request = call().catch((error: unknown) => error)
      await vi.advanceTimersByTimeAsync(timeoutMs)

      expect(observed.signal?.aborted).toBe(true)
      await expect(request).resolves.toMatchObject({
        name: 'ApiTimeoutError',
        timeoutMs,
      })
    },
  )

  it.each(bodyTimeoutCases)(
    'keeps the stalled %s under its %dms deadline through body consumption',
    async (_label, timeoutMs, status, call) => {
      vi.useFakeTimers()
      const observed: { signal?: AbortSignal } = {}
      vi.stubGlobal(
        'fetch',
        vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
          observed.signal = init?.signal ?? undefined
          return stalledBodyResponse(observed.signal, status)
        }),
      )

      const request = call().catch((error: unknown) => error)
      await vi.advanceTimersByTimeAsync(timeoutMs)

      expect(observed.signal?.aborted).toBe(true)
      await expect(request).resolves.toMatchObject({
        name: 'ApiTimeoutError',
        timeoutMs,
      })
    },
  )

  it.each([
    ['successful', 200],
    ['error', 502],
  ])('forwards a caller abort after headers while a %s JSON body is being read', async (_label, status) => {
    const source = new AbortController()
    const callerAbort = new DOMException('Caller cancelled', 'AbortError')
    const observed: { signal?: AbortSignal } = {}
    let markBodyRead!: () => void
    const bodyRead = new Promise<void>((resolve) => {
      markBodyRead = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        observed.signal = init?.signal ?? undefined
        return stalledBodyResponse(observed.signal, status, markBodyRead)
      }),
    )

    const request = api
      .trackerIssue('ROBOPARK-42', source.signal)
      .catch((error: unknown) => error)
    await bodyRead
    source.abort(callerAbort)

    expect(observed.signal?.aborted).toBe(true)
    expect(observed.signal?.reason).toBe(callerAbort)
    const rejection = await request

    expect(rejection).toBe(callerAbort)
    expect(rejection).not.toBeInstanceOf(ApiTimeoutError)
  })

  it('revalidates change revisions with ETag and reuses a 304 body', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response('{"revision":7}', { status: 200, headers: { ETag: '"7"' } }))
      .mockResolvedValueOnce(new Response(null, { status: 304 }))
    vi.stubGlobal('fetch', fetcher)

    await expect(api.changeRevision('work')).resolves.toEqual({ revision: 7 })
    await expect(api.changeRevision('work')).resolves.toEqual({ revision: 7 })

    expect(fetcher.mock.calls[1][1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ 'If-None-Match': '"7"' }),
    }))
  })
})
