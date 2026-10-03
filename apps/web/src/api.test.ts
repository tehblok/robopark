import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiTimeoutError, api, clearApiValidators } from './api'
import { systemClient } from './opsApi'

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

  it('binds a shared photo request to the account that claimed it', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      new Response('{}', { status: 201, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await api.taskPhoto('ROBOPARK-42', new File(['image'], 'robot.png', { type: 'image/png' }), 'share-photo-0001', 11)
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toMatchObject({
      'Idempotency-Key': 'share-photo-0001',
      'X-Expected-Account-Id': '11',
    })
  })

  it('passes an abort signal through resumable media requests', async () => {
    const controller = new AbortController()
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      await new Promise(resolve => setTimeout(resolve, 20))
      return new Response('{"received_offset":3}', { headers: { 'Content-Type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchMock)
    const request = api.putMediaChunk('upload-1', 0, new Blob(['abc']), 'digest', controller.signal)
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(false)
    controller.abort()
    expect(fetchMock.mock.calls[0]?.[1]?.signal?.aborted).toBe(true)
    await request
  })

  it('passes the report history boundary and filters to the API', async () => {
    const fetchMock = vi.fn(async () => new Response('[]', { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)

    await api.reportsMine({ limit: 26, beforeId: 52, anchorId: 77, status: 'returned' })
    await api.reportsInbox(7, { limit: 26, afterId: 52, anchorId: 77 })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/reports/mine?limit=26&anchor_id=77&before_id=52&status=returned', expect.any(Object))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/reports/inbox?park_id=7&limit=26&anchor_id=77&after_id=52', expect.any(Object))
  })

  it('sends schedule revision and retry keys through edit and delete requests', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('{"id":"entry/1"}', { headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    const options = { base_revision: '2026-09-20T10:00:00+00:00', idempotency_key: 'schedule-retry-key-0001' }

    await api.scheduleUpdate('entry/1', {
      kind: 'vacation',
      start_at: '2026-09-21T09:00:00+03:00',
      end_at: '2026-09-21T21:00:00+03:00',
      ...options,
    })
    await api.scheduleDelete('entry/1', options)

    expect(fetchMock.mock.calls[0][0]).toBe('/api/schedules/entry%2F1')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject(options)
    const deleteUrl = new URL(fetchMock.mock.calls[1][0], 'https://robopark.invalid')
    expect(deleteUrl.pathname).toBe('/api/schedules/entry%2F1')
    expect(Object.fromEntries(deleteUrl.searchParams)).toEqual(options)
    expect(fetchMock.mock.calls[1][1].method).toBe('DELETE')
  })

  it('loads every keyset page for one exact schedule scope', async () => {
    const schedule = (id: string, startAt: string) => ({
      id,
      owner_user_id: 7,
      park_id: 11,
      kind: 'shift' as const,
      start_at: startAt,
      end_at: '2026-09-01T12:00:00+03:00',
      source: 'self',
      series_id: null,
      created_by_user_id: 7,
      updated_by_user_id: 7,
      created_at: '2026-09-01T00:00:00Z',
      updated_at: '2026-09-01T00:00:00Z',
      warnings: [],
    })
    const first = schedule('first', '2026-09-01T09:00:00+03:00')
    const boundary = schedule('boundary', '2026-09-01T10:00:00+03:00')
    const last = schedule('last', '2026-09-01T11:00:00+03:00')
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify([first, boundary]), {
        headers: {
          'Content-Type': 'application/json',
          'X-Schedule-Has-More': 'true',
          'X-Schedule-Next-Start-At': boundary.start_at,
          'X-Schedule-Next-Id': boundary.id,
        },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([last]), {
        headers: { 'Content-Type': 'application/json', 'X-Schedule-Has-More': 'false' },
      }))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()

    const rows = await api.schedules({
      parkId: 11,
      ownerUserId: 7,
      startAt: '2026-09-01T00:00:00.000Z',
      endAt: '2026-10-01T00:00:00.000Z',
      signal: controller.signal,
    })

    expect(rows.map(row => row.id)).toEqual(['first', 'boundary', 'last'])
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const secondUrl = new URL(fetchMock.mock.calls[1][0], 'https://robopark.invalid')
    expect(Object.fromEntries(secondUrl.searchParams)).toEqual({
      start_at: '2026-09-01T00:00:00.000Z',
      end_at: '2026-10-01T00:00:00.000Z',
      park_id: '11',
      owner_user_id: '7',
      limit: '2000',
      after_start_at: boundary.start_at,
      after_id: boundary.id,
    })
    expect(fetchMock.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal)
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

  it('does not reuse a captured tracker payload when a 304 races authorization cleanup', async () => {
    let complete!: (response: Response) => void
    const oldAccountRevalidation = new Promise<Response>(resolve => { complete = resolve })
    const oldPayload = { items: [{ key: 'OLD-1' }], total: 1, limit: 50, offset: 0, has_more: false }
    const newPayload = { items: [{ key: 'NEW-1' }], total: 1, limit: 50, offset: 0, has_more: false }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(oldPayload), {
        status: 200, headers: { 'Content-Type': 'application/json', ETag: '"old-account"' },
      }))
      .mockReturnValueOnce(oldAccountRevalidation)
      .mockResolvedValueOnce(new Response(JSON.stringify(newPayload), {
        status: 200, headers: { 'Content-Type': 'application/json', ETag: '"new-account"' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    await api.trackerIssues({ limit: 50 })
    const oldAccountRequest = api.trackerIssues({ limit: 50 })
    clearApiValidators()
    complete(new Response(null, { status: 304 }))

    await expect(oldAccountRequest).resolves.toEqual(newPayload)
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/tracker/issues?sort=oldest&limit=50',
      expect.objectContaining({ headers: {} }))
  })

  it('does not retain a tracker response larger than the validator byte budget', async () => {
    const oversizedPayload = {
      items: [], total: 0, limit: 50, offset: 0, has_more: false,
      padding: 'x'.repeat(4 * 1024 * 1024),
    }
    const freshPayload = { items: [], total: 0, limit: 50, offset: 0, has_more: false }
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(oversizedPayload), {
        status: 200, headers: { 'Content-Type': 'application/json', ETag: '"oversized"' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(freshPayload), {
        status: 200, headers: { 'Content-Type': 'application/json', ETag: '"fresh"' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    await api.trackerIssues({ limit: 50 })
    await api.trackerIssues({ limit: 50 })

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/tracker/issues?sort=oldest&limit=50',
      expect.objectContaining({ headers: {} }))
  })

  it('keeps a local mutation owner mounted when a write is forbidden', async () => {
    const authorizationFailure = vi.fn()
    window.addEventListener('robopark:authorization-failure', authorizationFailure)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'forbidden' }),
      { status: 403, headers: { 'Content-Type': 'application/json' } },
    )))

    await expect(api.reportDelete(9)).rejects.toMatchObject({ status: 403 })
    expect(authorizationFailure).not.toHaveBeenCalled()

    window.removeEventListener('robopark:authorization-failure', authorizationFailure)
  })

  it('keeps the authenticated session after a privileged challenge is rejected', async () => {
    const authorizationFailure = vi.fn()
    window.addEventListener('robopark:authorization-failure', authorizationFailure)
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'invalid_credentials' }), {
        status: 401, headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: 7, username: 'royal' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    try {
      await expect(systemClient.reauthorize({
        password: 'wrong-password',
        code: '000000',
        operation_kind: 'service-restart',
        operation_id: 'service-restart-1',
        capability_revision: 'a'.repeat(64),
      })).rejects.toMatchObject({ status: 401, detail: 'invalid_credentials' })
      expect(authorizationFailure).not.toHaveBeenCalled()
      await expect(api.me()).resolves.toMatchObject({ id: 7, username: 'royal' })
      expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/me', expect.any(Object))
    } finally {
      window.removeEventListener('robopark:authorization-failure', authorizationFailure)
    }
  })

  it('invalidates an expired session rejected by the privileged challenge endpoint', async () => {
    const authorizationFailure = vi.fn()
    window.addEventListener('robopark:authorization-failure', authorizationFailure)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Unauthorized' }), {
      status: 401, headers: { 'Content-Type': 'application/json' },
    })))

    try {
      await expect(systemClient.reauthorize({
        password: 'secret',
        code: '123456',
        operation_kind: 'service-restart',
        operation_id: 'service-restart-1',
        capability_revision: 'a'.repeat(64),
      })).rejects.toMatchObject({ status: 401 })
      expect(authorizationFailure).toHaveBeenCalledOnce()
    } finally {
      window.removeEventListener('robopark:authorization-failure', authorizationFailure)
    }
  })

  it('classifies emergency resolve as a query POST and invalidates late protected validators on 403', async () => {
    let completeOldRequest!: (response: Response) => void
    const oldRequest = new Promise<Response>(resolve => { completeOldRequest = resolve })
    const authorizationFailure = vi.fn()
    window.addEventListener('robopark:authorization-failure', authorizationFailure)
    const payload = { items: [], total: 0, limit: 50, offset: 0, has_more: false }
    const fetchMock = vi.fn()
      .mockReturnValueOnce(oldRequest)
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'forbidden' }), {
        status: 403, headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      }))
    vi.stubGlobal('fetch', fetchMock)

    const late = api.trackerIssues({ limit: 50 })
    await expect(api.emergencyResolve('447')).rejects.toMatchObject({ status: 403 })
    expect(authorizationFailure).toHaveBeenCalledOnce()
    completeOldRequest(new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json', ETag: '"old-principal"' },
    }))
    await late
    await api.trackerIssues({ limit: 50 })

    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/emergency/resolve', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ robot_number: '447' }),
    }))
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/tracker/issues?sort=oldest&limit=50',
      expect.objectContaining({ headers: {} }))
    window.removeEventListener('robopark:authorization-failure', authorizationFailure)
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
      cache: 'no-store',
      headers: expect.objectContaining({ 'If-None-Match': '"7"' }),
    }))
  })

  it('does not conditionally revalidate an expired change revision without its body', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-03T00:00:00Z'))
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response('{"revision":7}', { status: 200, headers: { ETag: '"7"' } }))
      .mockResolvedValueOnce(new Response('{"revision":8}', { status: 200, headers: { ETag: '"8"' } }))
    vi.stubGlobal('fetch', fetcher)

    await expect(api.changeRevision('work')).resolves.toEqual({ revision: 7 })
    await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000)
    await expect(api.changeRevision('work')).resolves.toEqual({ revision: 8 })

    expect(fetcher.mock.calls[1][1]).toEqual(expect.objectContaining({ headers: {} }))
  })

  it.each(['revision', 'tracker'] as const)('keeps newer %s data when an older 304 completes last', async kind => {
    let complete!: (response: Response) => void
    const delayed = new Promise<Response>(resolve => { complete = resolve })
    const oldValue = kind === 'revision' ? { revision: 7 } : { items: [], total: 7 }
    const newValue = kind === 'revision' ? { revision: 8 } : { items: [], total: 8 }
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(oldValue), { headers: { ETag: '"v1"' } }))
      .mockReturnValueOnce(delayed)
      .mockResolvedValueOnce(new Response(JSON.stringify(newValue), { headers: { ETag: '"v2"' } }))
    vi.stubGlobal('fetch', fetcher)
    const read = () => kind === 'revision' ? api.changeRevision('work') : api.trackerIssues({ limit: 50 })
    await read()
    const older = read()
    await expect(read()).resolves.toEqual(newValue)
    complete(new Response(null, { status: 304 }))
    await expect(older).resolves.toEqual(newValue)
  })

  it.each([
    ['revision', true],
    ['revision', false],
    ['tracker', true],
    ['tracker', false],
  ] as const)('keeps newer %s data when an older 200 with ETag=%s completes last', async (kind, oldHasEtag) => {
    let complete!: (response: Response) => void
    const delayed = new Promise<Response>(resolve => { complete = resolve })
    const value = (revision: number) => kind === 'revision'
      ? { revision }
      : { items: [], total: revision, limit: 50, offset: 0, has_more: false }
    const fetcher = vi.fn()
      .mockReturnValueOnce(delayed)
      .mockResolvedValueOnce(new Response(JSON.stringify(value(2)), { headers: { ETag: '"v2"' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(value(3)), { headers: { ETag: '"v3"' } }))
    vi.stubGlobal('fetch', fetcher)
    const read = () => kind === 'revision' ? api.changeRevision('work') : api.trackerIssues({ limit: 50 })

    const older = read()
    await expect(read()).resolves.toEqual(value(2))
    complete(new Response(JSON.stringify(value(1)), {
      headers: oldHasEtag ? { ETag: '"v1"' } : {},
    }))

    await expect(older).resolves.toEqual(value(2))
    await read()
    expect(fetcher.mock.calls[2][1]).toEqual(expect.objectContaining({
      headers: { 'If-None-Match': '"v2"' },
    }))
  })

  it('does not conditionally revalidate an evicted change revision without its body', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const scope = new URL(String(input), 'https://robopark.invalid').searchParams.get('scope')
      return new Response('{"revision":1}', { status: 200, headers: { ETag: `"${scope}"` } })
    })
    vi.stubGlobal('fetch', fetcher)

    for (let index = 0; index <= 128; index += 1) await api.changeRevision(`inventory:${index}`)
    await api.changeRevision('inventory:0')

    expect(fetcher.mock.calls[129][1]).toEqual(expect.objectContaining({ headers: {} }))
  })

  it('forgets a change validator when a successful response no longer has an ETag', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response('{"revision":7}', { status: 200, headers: { ETag: '"7"' } }))
      .mockResolvedValueOnce(new Response('{"revision":8}', { status: 200 }))
      .mockResolvedValueOnce(new Response('{"revision":9}', { status: 200 }))
    vi.stubGlobal('fetch', fetcher)

    await api.changeRevision('work')
    await api.changeRevision('work')
    await api.changeRevision('work')

    expect(fetcher.mock.calls[1][1]).toEqual(expect.objectContaining({
      headers: { 'If-None-Match': '"7"' },
    }))
    expect(fetcher.mock.calls[2][1]).toEqual(expect.objectContaining({ headers: {} }))
  })

  it('does not reuse a captured change revision when a 304 races authorization cleanup', async () => {
    let complete!: (response: Response) => void
    const oldAccountRevalidation = new Promise<Response>(resolve => { complete = resolve })
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response('{"revision":7}', { status: 200, headers: { ETag: '"old-account"' } }))
      .mockReturnValueOnce(oldAccountRevalidation)
      .mockResolvedValueOnce(new Response('{"revision":9}', { status: 200, headers: { ETag: '"new-account"' } }))
    vi.stubGlobal('fetch', fetcher)

    await api.changeRevision('work')
    const oldAccountRequest = api.changeRevision('work')
    clearApiValidators()
    complete(new Response(null, { status: 304 }))
    await expect(oldAccountRequest).resolves.toEqual({ revision: 9 })

    expect(fetcher.mock.calls[2][1]).toEqual(expect.objectContaining({ headers: {} }))
  })
})
