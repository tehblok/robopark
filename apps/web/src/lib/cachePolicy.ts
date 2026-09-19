export type DeviceCachePolicy = {
  ttlMs: number
  staleMs: number
  maxEntries: number
  maxBytes: number
}

export const DEFAULT_DEVICE_CACHE_POLICY: DeviceCachePolicy = {
  ttlMs: 30_000,
  staleMs: 12 * 60 * 60 * 1000,
  maxEntries: 256,
  maxBytes: 32 * 1024 * 1024,
}

export function cachePolicy(overrides: Partial<DeviceCachePolicy> = {}): DeviceCachePolicy {
  const policy = { ...DEFAULT_DEVICE_CACHE_POLICY, ...overrides }
  if (policy.ttlMs <= 0 || policy.staleMs < policy.ttlMs || policy.maxEntries <= 0 || policy.maxBytes <= 0) {
    throw new Error('Invalid bounded device cache policy')
  }
  return policy
}
