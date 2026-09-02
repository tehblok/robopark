import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiTimeoutError, api } from './api'

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
    vi.useRealTimers()
    vi.unstubAllGlobals()
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
})
