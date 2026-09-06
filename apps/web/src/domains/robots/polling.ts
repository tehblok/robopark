export const ROBOT_POLL_MS = 10_000
export const POLL_DELAYS_MS = [10_000, 20_000, 40_000, 60_000, 120_000] as const
export function pollDelayAfterFailure(consecutiveFailures: number): number {
  return POLL_DELAYS_MS[Math.min(Math.max(0, Math.floor(consecutiveFailures)), POLL_DELAYS_MS.length - 1)]
}
