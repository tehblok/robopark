export const OPTIMISTIC_BATCH_DELAY_MS = 150

type Projection = { actionId: string, resource: string, value: unknown }

/** Keeps the next render ahead of network dispatch and coalesces nearby writes. */
export class ClientBatcher {
  private readonly flush: () => Promise<unknown>
  private readonly delayMs: number
  private timer: ReturnType<typeof setTimeout> | null = null
  private readonly projections: Projection[] = []
  private readonly listeners = new Set<() => void>()

  constructor(flush: () => Promise<unknown>, delayMs = OPTIMISTIC_BATCH_DELAY_MS) {
    this.flush = flush
    this.delayMs = delayMs
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  project(actionId: string, resource: string, value: unknown): void {
    this.projections.push({ actionId, resource, value })
    this.publish()
  }

  getProjection(resource: string): unknown {
    return this.projections.findLast(item => item.resource === resource)?.value
  }

  settle(actionId: string): void {
    const index = this.projections.findIndex(item => item.actionId === actionId)
    if (index < 0) return
    this.projections.splice(index, 1)
    this.publish()
  }

  schedule(): void {
    if (this.timer !== null) return
    this.timer = setTimeout(() => { this.timer = null; void this.flush() }, this.delayMs)
  }

  cancelScheduled(): void {
    if (this.timer === null) return
    clearTimeout(this.timer)
    this.timer = null
  }

  dispose(): void {
    this.cancelScheduled()
    this.projections.length = 0
    this.listeners.clear()
  }

  private publish(): void { for (const listener of this.listeners) listener() }
}
