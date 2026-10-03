import { ApiError, ApiTimeoutError } from '../../api'

type RequestMetric = { requests: number; errors: number; limited: number; average_ms: number | null; max_ms: number | null }
export type HostHealth = {
  sampled_at: number; database: string; window_seconds: number
  host_health_source_state?: 'reported' | 'unavailable'
  disk: { total_bytes: number | null; free_bytes: number | null; source_state?: 'measured' | 'unavailable' }
  memory: { total_bytes: number | null; available_bytes: number | null; container_limit_bytes: number | null; container_used_bytes: number | null }
  backup: { verified_at: number | null; overdue: boolean; last_attempt_failed: boolean }
  requests: Partial<Record<'tracker' | 'diagnostics' | 'reports', RequestMetric>>
  storage?: { floor_bytes: number; bytes_to_reclaim: number | null; space_pressure?: boolean | null; category_bytes: Record<string, number>; last_cleanup_at: number | null; cleanup_failed: boolean | null; cleanup_busy?: boolean; api_last_cleanup_at?: number | null; api_cleanup_failed?: boolean; api_cleanup_owners?: Record<string, { deleted_count: number; batches: number }> }
  process?: { rss_bytes: number | null; rss_trend_bytes: number; open_fds: number | null; tasks: number; threads: number; cache_bytes: number; db_pool_checked_out: number | null; memory_pressure?: { sustained: boolean; evicted: boolean; failed: boolean } }
  capabilities?: { source_state?: 'reported' | 'missing' | 'invalid' | 'stale'; profile: string; jpeg_backend: string; hardware_jpeg: boolean; npu_available: boolean; cuda_available: boolean; nvme_available?: boolean }
}
export const hostHealthApi = {
  async get(): Promise<HostHealth> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 20_000)
    try {
      const response = await fetch('/api/admin/health', { credentials: 'include', signal: controller.signal })
      if (!response.ok) {
        const retry = response.headers.get('Retry-After')
        const delay = retry == null ? undefined : Number.isFinite(Number(retry)) ? Number(retry) * 1000 : Math.max(0, Date.parse(retry) - Date.now())
        throw new ApiError(response.status, null, undefined, delay)
      }
      return await response.json() as HostHealth
    } catch (error) {
      if (controller.signal.aborted) throw new ApiTimeoutError(20_000)
      throw error
    } finally { clearTimeout(timer) }
  },
}
