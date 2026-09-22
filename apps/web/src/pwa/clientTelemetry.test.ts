import { describe, expect, it, vi } from 'vitest'
import { ClientTelemetry, type ClientMetric } from './clientTelemetry'

describe('ClientTelemetry', () => {
  it('starts and disposes with browser timers that require the window receiver', () => {
    let canceled = false
    vi.stubGlobal('setTimeout', function (this: unknown) {
      if (this !== globalThis) throw new TypeError('Illegal invocation')
      return 42 as never
    })
    vi.stubGlobal('clearTimeout', function (this: unknown, timer: number) {
      if (this !== globalThis) throw new TypeError('Illegal invocation')
      canceled = timer === 42
    })
    try {
      const telemetry = new ClientTelemetry()
      telemetry.dispose()
      expect(canceled).toBe(true)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('samples ordinary metrics, bounds batches and never accepts arbitrary payloads', async () => {
    const send = vi.fn(async (_batch: { metrics: ClientMetric[] }) => undefined)
    const telemetry = new ClientTelemetry({ send, random: () => 0, sampleRate: 1, schedule: () => 0 as never, cancel: vi.fn() })
    for (let index = 0; index < 25; index += 1) telemetry.record('queue_length', index)
    // @ts-expect-error protected/arbitrary metric names are rejected by the type contract
    telemetry.record('task_text', 1)
    await telemetry.flush()
    expect(send).toHaveBeenCalledTimes(2)
    expect(send.mock.calls.flatMap(call => call[0].metrics)).toHaveLength(25)
    telemetry.dispose()
  })

  it('always keeps migration failures and releases its timer on dispose', () => {
    const cancel = vi.fn()
    const telemetry = new ClientTelemetry({
      send: vi.fn(async () => undefined), random: () => 1, sampleRate: 0,
      schedule: () => 42 as never, cancel,
    })
    telemetry.record('startup_ms', 100)
    telemetry.record('migration_failure', 1)
    expect(telemetry.pending()).toEqual([{ name: 'migration_failure', value: 1 }])
    telemetry.dispose()
    expect(cancel).toHaveBeenCalledWith(42)
  })

  it('re-arms periodic delivery and retains metrics after a temporary failure', async () => {
    const callbacks: Array<() => void> = []
    const send = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined)
    const telemetry = new ClientTelemetry({
      send, random: () => 0, sampleRate: 1,
      schedule: callback => { callbacks.push(callback); return callbacks.length as never },
      cancel: vi.fn(),
    })
    telemetry.record('queue_length', 2)
    callbacks.shift()?.()
    await vi.waitFor(() => expect(callbacks).toHaveLength(1))
    expect(telemetry.pending()).toEqual([{ name: 'queue_length', value: 2 }])
    callbacks.shift()?.()
    await vi.waitFor(() => expect(telemetry.pending()).toEqual([]))
    expect(callbacks).toHaveLength(1)
    telemetry.dispose()
  })
})
