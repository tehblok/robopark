import { ApiError, ApiTimeoutError } from '../../api'

type RequestMetric = { requests: number; errors: number; limited: number; average_ms: number | null; max_ms: number | null }
export type HostHealth = {
  sampled_at: number; database: string; window_seconds: number
  disk: { total_bytes: number | null; free_bytes: number | null }
  memory: { total_bytes: number | null; available_bytes: number | null; container_limit_bytes: number | null; container_used_bytes: number | null }
  backup: { verified_at: number | null; overdue: boolean; last_attempt_failed: boolean }
  requests: Partial<Record<'tracker' | 'diagnostics' | 'reports', RequestMetric>>
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
