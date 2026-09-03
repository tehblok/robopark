export const POLL_DELAYS_MS = [2500, 5000, 10000, 20000, 30000] as const
export function pollDelayAfterFailure(consecutiveFailures: number): number {
  return POLL_DELAYS_MS[Math.min(Math.max(0, Math.floor(consecutiveFailures)), POLL_DELAYS_MS.length - 1)]
}
