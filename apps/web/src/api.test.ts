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

  it('preserves an AbortError that was not caused by the transport deadline', async () => {
    const callerAbort = new DOMException('Caller cancelled', 'AbortError')
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(callerAbort)))

    const rejection = await api.trackerIssue('ROBOPARK-42').catch((error: unknown) => error)

    expect(rejection).toBe(callerAbort)
    expect(rejection).not.toBeInstanceOf(ApiTimeoutError)
  })
})
