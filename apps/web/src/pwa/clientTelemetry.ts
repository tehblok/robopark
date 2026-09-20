export type ClientMetricName = 'startup_ms' | 'queue_length' | 'storage_bytes' | 'retry_count' | 'migration_failure'
export type ClientMetric = { name: ClientMetricName, value: number }

type Options = {
  send?: (batch: { metrics: ClientMetric[] }) => Promise<void>
  random?: () => number
  sampleRate?: number
  schedule?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>
  cancel?: (timer: ReturnType<typeof setTimeout>) => void
  intervalMs?: number
}

const NAMES = new Set<ClientMetricName>(['startup_ms', 'queue_length', 'storage_bytes', 'retry_count', 'migration_failure'])
const MAX_BUFFER = 50
const MAX_BATCH = 20

async function defaultSend(batch: { metrics: ClientMetric[] }): Promise<void> {
  const response = await fetch('/client-telemetry', {
    method: 'POST', credentials: 'include', keepalive: true,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(batch),
  })
  if (!response.ok) throw new Error('client_telemetry_failed')
}

export class ClientTelemetry {
  private readonly send: NonNullable<Options['send']>
  private readonly random: () => number
  private readonly sampleRate: number
  private readonly schedule: NonNullable<Options['schedule']>
  private readonly cancel: NonNullable<Options['cancel']>
  private readonly intervalMs: number
  private readonly buffer: ClientMetric[] = []
  private timer: ReturnType<typeof setTimeout> | null
  private disposed = false

  constructor(options: Options = {}) {
    this.send = options.send ?? defaultSend
    this.random = options.random ?? Math.random
    this.sampleRate = options.sampleRate ?? 0.1
    this.schedule = options.schedule ?? setTimeout
    this.cancel = options.cancel ?? clearTimeout
    this.intervalMs = options.intervalMs ?? 30_000
    this.timer = null
    this.arm()
  }

  private arm(): void {
    if (this.disposed || this.timer != null) return
    this.timer = this.schedule(() => {
      this.timer = null
      void this.flush().catch(() => undefined).finally(() => this.arm())
    }, this.intervalMs)
  }

  record(name: ClientMetricName, value: number): void {
    if (this.disposed || !NAMES.has(name) || !Number.isFinite(value) || value < 0) return
    if (name !== 'migration_failure' && this.random() >= this.sampleRate) return
    if (this.buffer.length >= MAX_BUFFER) this.buffer.shift()
    this.buffer.push({ name, value })
  }

  pending(): ClientMetric[] { return [...this.buffer] }

  async flush(): Promise<void> {
    while (!this.disposed && this.buffer.length) {
      const batch = this.buffer.slice(0, MAX_BATCH)
      await this.send({ metrics: batch })
      this.buffer.splice(0, batch.length)
    }
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    if (this.timer != null) this.cancel(this.timer)
    this.timer = null
  }
}
