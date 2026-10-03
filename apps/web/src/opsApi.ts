import { ApiError, fetchWithTimeout } from './api'

export type HealthState = 'ok' | 'degraded' | 'unknown'
export type SystemSummary = {
  sampled_at: string | null
  metrics_stale: boolean
  worker_health: 'worker_healthy' | 'worker_heartbeat_missing' | 'worker_heartbeat_stale' | 'worker_metric_missing' | 'worker_metric_stale' | 'worker_metric_future'
  online: { total: number; by_role: Record<string, number>; by_park: Record<string, number> }
  tracker?: { state: 'configured' | 'not_configured' | 'credential_unavailable' }
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
  'ota-update', 'rollback', 'package-inspect', 'package-update',
  'service-restart', 'reboot', 'backup', 'backup-verify', 'backup-restore',
  'cleanup-preview', 'cleanup-execute', 'docker-image-preview', 'docker-image-execute',
  'builder-cache-preview', 'builder-cache-execute', 'diagnostics', 'usb-discover', 'usb-format',
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
  artifact_ready?: boolean
  created_at?: string
  updated_at?: string
  progress_percent: number | null; error: string | null
  host_result?: {
    devices?: Array<{ device_uuid: string; removable: boolean; mounted: boolean }>
    cleanup_preview?: {
      plan_id: string; blocked: boolean; total_bytes: number
      planned: Array<{ category: 'diagnostics' | 'logs' | 'backups' | 'releases' | 'ota_cache'; path: string; bytes: number }>
    } | null
    cleanup_result?: {
      deleted: Array<{ category: 'diagnostics' | 'logs' | 'backups' | 'releases' | 'ota_cache'; path: string; bytes: number }>
      deleted_count: number
      uncertain_target: { category: 'diagnostics' | 'logs' | 'backups' | 'releases' | 'ota_cache'; path: string; bytes: number } | null
    } | null
    docker_image_preview?: {
      plan_id: string | null
      blocked: boolean
      planned: Array<{ tag: string; reported_bytes: number }>
      total_reported_bytes: number
      unverified_tags: number
    } | null
    docker_image_result?: {
      deleted: Array<{ tag: string; reported_bytes: number }>
      deleted_count: number
      uncertain_target: { tag: string; reported_bytes: number } | null
    } | null
    builder_cache_preview?: {
      plan_id: string | null
      blocked: boolean
      planned: Array<{ id: string; reported_bytes: number }>
      total_reported_bytes: number
      other_candidates: number
    } | null
    builder_cache_result?: {
      deleted: Array<{ id: string; reported_bytes: number }>
      deleted_count: number
      uncertain_target: { id: string; reported_bytes: number } | null
    } | null
    package_result?: {
      package: string; installed: boolean | null; version: string | null; updated: boolean | null
    } | null
    backup_result?: {
      backup_id: string; verified: boolean | null; restored: boolean | null
    } | null
    action_result?: {
      release?: string | null; rolled_back?: boolean | null
      service?: string | null; restarted?: boolean | null; reboot_scheduled?: boolean | null
    } | null
    usb_result?: {
      device_uuid: string; selected: boolean | null; formatted: boolean | null
    } | null
    diagnostics_ready?: boolean
  } | null
}
export type HostOperationContext = {
  generated_at: string
  expires_at: string
  selected_device_uuid: string | null
  rollback_release: string | null
  packages: string[]
  services: string[]
  devices: Array<{ device_uuid: string; removable: boolean; mounted: boolean; path?: string | null; bytes?: number | null }>
  backups: Array<{
    backup_id: string; bytes: number; verified: boolean; created_at: string | null
  }>
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
  getOperations: () => Promise<{ items: SystemJob[] }>
  getOperationContext: () => Promise<HostOperationContext>
  operationArtifactUrl: (operationId: string) => string
  reauthorize: (value: ReauthorizationInput) => Promise<{ token: string; expires_in: number }>
  startOperation: (value: HostOperationPayload, token: string) => Promise<SystemJob>
}

export type OtaUpload = {
  upload_id: string
  filename: string
  size: number
  sha256: string
  offset: number
  expires_at: number
  state: 'uploading' | 'verified'
  chunk_size: number
  version?: string | null
  changes?: string[]
  compatible_from?: string[]
  required_free_bytes?: number | null
  already_present?: boolean
}

export type OtaUploadClient = {
  list: () => Promise<{ items: OtaUpload[] }>
  create: (value: { filename: string; size: number; sha256: string }) => Promise<OtaUpload>
  offset: (uploadId: string) => Promise<number>
  append: (uploadId: string, offset: number, chunk: Blob) => Promise<number>
  finalize: (uploadId: string) => Promise<OtaUpload>
  remove: (uploadId: string) => Promise<void>
}

async function json<T>(
  path: string,
  init: RequestInit = {},
  metadata?: Parameters<typeof fetchWithTimeout>[4],
): Promise<T> {
  return fetchWithTimeout(`/api${path}`, {
    credentials: 'include',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  }, 15_000, async response => {
    if (!response.ok) {
      let detail: unknown = null
      try { detail = (await response.json()).detail ?? null } catch { /* bounded error */ }
      throw new ApiError(response.status, detail)
    }
    return await response.json() as T
  }, metadata)
}

export const systemClient: SystemClient = {
  getSummary: () => json('/admin/system/summary'),
  getHistory: () => json('/admin/system/history?days=7'),
  getCapabilities: () => json('/admin/ops/capabilities'),
  getOperation: operationId => json(`/admin/ops/operations/${encodeURIComponent(operationId)}`),
  getOperations: () => json('/admin/ops/operations'),
  getOperationContext: () => json('/admin/ops/operation-context'),
  operationArtifactUrl: operationId => `/api/admin/ops/operations/${encodeURIComponent(operationId)}/artifact`,
  reauthorize: value => json('/admin/privileged-auth/reauthorize', {
    method: 'POST', body: JSON.stringify(value),
  }, { sessionPreserving401Details: ['invalid_credentials'] }),
  startOperation: (value, token) => json('/admin/ops/operations', {
    method: 'POST',
    headers: { 'X-Privileged-Authorization': token },
    body: JSON.stringify(value),
  }),
}

async function uploadRequest(path: string, init: RequestInit): Promise<Response> {
  return fetchWithTimeout(`/api${path}`, { credentials: 'include', ...init }, 120_000, async response => {
    if (!response.ok) {
      let detail: unknown = null
      try { detail = (await response.json()).detail ?? null } catch { /* bounded error */ }
      throw new ApiError(response.status, detail)
    }
    return response
  })
}

export const otaUploadClient: OtaUploadClient = {
  list: () => json('/admin/ops/ota/uploads'),
  create: async value => (await uploadRequest('/admin/ops/ota/uploads', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value),
  })).json(),
  offset: async uploadId => {
    const response = await uploadRequest(`/admin/ops/ota/uploads/${encodeURIComponent(uploadId)}`, { method: 'HEAD' })
    const value = Number(response.headers.get('Upload-Offset'))
    if (!Number.isSafeInteger(value) || value < 0) throw new Error('ota_offset_invalid')
    return value
  },
  append: async (uploadId, offset, chunk) => {
    const response = await uploadRequest(`/admin/ops/ota/uploads/${encodeURIComponent(uploadId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/offset+octet-stream', 'Upload-Offset': String(offset) },
      body: chunk,
    })
    const value = (await response.json()) as { offset?: unknown }
    if (!Number.isSafeInteger(value.offset) || Number(value.offset) < 0) throw new Error('ota_offset_invalid')
    return Number(value.offset)
  },
  finalize: async uploadId => (await uploadRequest(`/admin/ops/ota/uploads/${encodeURIComponent(uploadId)}/finalize`, { method: 'POST' })).json(),
  remove: async uploadId => { await uploadRequest(`/admin/ops/ota/uploads/${encodeURIComponent(uploadId)}`, { method: 'DELETE' }) },
}
