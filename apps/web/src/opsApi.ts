import { ApiError } from './api'

export type HealthState = 'ok' | 'degraded' | 'unknown'
export type SystemSummary = {
  sampled_at: string | null
  metrics_stale: boolean
  online: { total: number; by_role: Record<string, number>; by_park: Record<string, number> }
  sync: {
    cursor_age_seconds: number | null; pending_action_count: number
    oldest_pending_action_age_seconds: number | null; retry_count: number
    needs_attention_count: number; last_success_at: string | null
    last_error: string | null; worker_lease_state: 'active' | 'stale' | 'unknown'
  }
  push: { pending: number; needs_attention: number }
  metrics: { host?: Record<string, unknown> } | null
  release: Record<string, unknown>
}
export type SystemHistory = {
  active_users: { date: string; users: number }[]
  metrics: Record<string, unknown>[]
}
export const HOST_OPERATION_KINDS = [
  'release-update', 'reinstall', 'rollback', 'package-inspect', 'package-update',
  'service-restart', 'reboot', 'backup', 'backup-verify', 'backup-restore',
  'cleanup-preview', 'cleanup-execute', 'diagnostics', 'usb-discover', 'usb-format',
  'usb-select',
] as const
export type HostOperationKind = typeof HOST_OPERATION_KINDS[number]
export type HostCapabilities = {
  state: 'ready' | 'unavailable'
  generated_at: string | null
  expires_at: string | null
  revision: string | null
  operations: Record<HostOperationKind, {
    available: boolean
    unavailable_reason: 'capability_unavailable' | 'capabilities_unavailable' | 'context_unavailable' | null
  }>
}
export type SystemJob = {
  id: string; kind: string; state: string; phase: string
  receipt_state?: 'received' | 'accepted' | 'terminal'
  progress_percent: number | null; error: string | null
  host_result?: { devices?: Array<{ device_uuid: string; removable: boolean; mounted: boolean }> } | null
}
export type ReauthorizationInput = {
  password: string; code: string; operation_kind: HostOperationKind
  operation_id: string; capability_revision: string
}
export type HostOperationPayload = Record<string, unknown> & {
  operation_id: string; kind: HostOperationKind; capability_revision: string
  confirmation: string
}
export type SystemClient = {
  getSummary: () => Promise<SystemSummary>
  getHistory: () => Promise<SystemHistory>
  getCapabilities: () => Promise<HostCapabilities>
  getOperation: (operationId: string) => Promise<SystemJob>
  reauthorize: (value: ReauthorizationInput) => Promise<{ token: string; expires_in: number }>
  startOperation: (value: HostOperationPayload, token: string) => Promise<SystemJob>
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  if (!response.ok) {
    let detail: unknown = null
    try { detail = (await response.json()).detail ?? null } catch { /* bounded error */ }
    throw new ApiError(response.status, detail)
  }
  return await response.json() as T
}

export const systemClient: SystemClient = {
  getSummary: () => json('/admin/system/summary'),
  getHistory: () => json('/admin/system/history?days=7'),
  getCapabilities: () => json('/admin/ops/capabilities'),
  getOperation: operationId => json(`/admin/ops/operations/${encodeURIComponent(operationId)}`),
  reauthorize: value => json('/admin/privileged-auth/reauthorize', {
    method: 'POST', body: JSON.stringify(value),
  }),
  startOperation: (value, token) => json('/admin/ops/operations', {
    method: 'POST',
    headers: { 'X-Privileged-Authorization': token },
    body: JSON.stringify(value),
  }),
}
