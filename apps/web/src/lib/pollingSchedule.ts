/** Positive jitter never raises the configured steady-state request rate. */
export function periodicDelay(baseMs: number, initial = false): number {
  return baseMs + Math.floor(Math.random() * baseMs * (initial ? 1 : 0.2))
}

/** Spread a fleet of reconnecting clients across thirty seconds. */
export function resumeDelay(): number {
  return Math.floor(Math.random() * 30_000)
}

export function retryAfterMs(error: unknown): number {
  if (!error || typeof error !== 'object' || !('retryAfterMs' in error)) return 0
  const delay = error.retryAfterMs
  return typeof delay === 'number' && Number.isFinite(delay) ? Math.max(0, delay) : 0
}
