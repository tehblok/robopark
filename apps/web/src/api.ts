import type { AnalyticsBucket, HistoricalAnalytics } from './domains/analytics/analyticsModel'
import type {
  InventoryCatalogComponent,
  InventoryCatalogPart,
  InventoryCatalogSearchItem,
  InventoryCount,
  InventoryCountLineInput,
  InventoryCountScope,
  InventoryExportParams,
  InventoryApiErrorDetail,
  InventoryCountStaleErrorDetail,
  InventoryDuplicateErrorDetail,
  InventoryInt64,
  InventoryListParams,
  InventoryPageEnvelope,
  InventoryReceipt,
  InventoryReceiptInput,
  InventorySearchParams,
  InventoryStockView,
} from './domains/inventory/inventoryTypes'

export type {
  InventoryCatalogComponent,
  InventoryCatalogPart,
  InventoryCatalogSearchItem,
  InventoryCount,
  InventoryCountLine,
  InventoryCountLineInput,
  InventoryCountStaleErrorDetail,
  InventoryCountScope,
  InventoryDocumentStatus,
  InventoryDuplicateErrorDetail,
  InventoryInt64,
  InventoryApiErrorDetail,
  InventoryExportParams,
  InventoryListParams,
  InventoryPageEnvelope,
  InventoryReceipt,
  InventoryReceiptInput,
  InventoryReceiptLine,
  InventoryReceiptLineInput,
  InventorySearchParams,
  InventoryStockFilter,
  InventoryStockView,
} from './domains/inventory/inventoryTypes'

export type Park = {
  id: number
  name: string
  tag: string
  is_active?: boolean
  tracker_queue?: string | null
  tracker_priority?: string | null
  tracker_type?: string | null
  group_id?: number | null
  chat_id?: number | null
  feature_reports?: boolean
  feature_blockers?: boolean
  feature_sla_repair?: boolean
  feature_backlog_alerts?: boolean
}

export type AccessRequest = {
  id: number
  username: string
  role: string
  access_status: string
}

export type ParkRequest = {
  id: number
  user_id: number
  park_id: number
  status: string
  created_at: string
  resolved_at: string | null
  resolved_by: number | null
  username?: string | null
}

export type User = {
  id: number
  username: string
  role: string
  access_status: string
  permissions?: string[]
  tracker_login?: string | null
  must_change_password?: boolean
  screenshot_guard?: boolean
  parks: Park[]
}

export type RobotRegistryRow = {
  vin: string; short_number: string; park_ids: number[]; task_count: number; task_keys: string[]; issue_keys: string[]
  state: 'online' | 'offline' | 'unknown'; error_count: number | null
  telemetry: { source: 'emergency_cache'; online: boolean | null; charge_percent: number | null; mode: string | null; connection: string | null } | null
}
export type RobotRegistryParams = { park_id?: number; query?: string; state?: string; active_errors?: boolean; open_tasks?: boolean; offset?: number; limit?: number }
export type RobotRegistry = { items: RobotRegistryRow[]; total: number; offset: number; limit: number; has_more: boolean; partial: boolean; source_complete: boolean; source: 'scoped_tracker_issues'; park_id: number | null }

export type AdminRole = {
  id: number
  slug: string
  name: string
  description: string
  is_system: boolean
  is_active: boolean
  permissions: string[]
  user_count: number
}

export type PermissionCatalogItem = {
  key: string
  category: string
  label: string
  sort_order: number
}

export type AdminUser = {
  id: number
  username: string
  role: string
  role_id: number
  access_status: string
  is_active: boolean
  tracker_login?: string | null
  must_change_password?: boolean
  parks: Park[]
  permissions: string[]
  role_permissions: string[]
  last_seen_at?: string | null
  last_ip?: string | null
  last_device?: string | null
  last_location?: string | null
}

export type IntegrationSettings = {
  tracker_token_masked: string | null
  tracker_token_updated_at: string | null
  tracker_token_encrypted?: boolean
  emergency_cookie_masked: string | null
  emergency_cookie_updated_at: string | null
  emergency_cookie_encrypted?: boolean
  emergency_cookie_valid: boolean | null
  emergency_cookie_status: 'unchecked' | 'valid' | 'invalid' | 'unavailable'
  emergency_cookie_checked_at: string | null
  emergency_cookie_checked_robot: string | null
}

export type TrackerPolicySettings = {
  operator_show_untagged: boolean
  operator_show_raw: boolean
  operator_show_firmware_profile: boolean
  mechanic_can_write: boolean
}

export type ScreenshotGuardSettings = {
  operator: boolean
  mechanic: boolean
  admin: boolean
  royal: boolean
  driver: boolean
}

export type RegistrationPasswordSettings = {
  configured: boolean
  password_masked: string | null
  updated_at: string | null
  encrypted?: boolean
}

export type Mechanic = {
  id: number
  username: string
  is_active: boolean
  created_at: string
  tracker_login?: string | null
  must_change_password?: boolean
  park: Park
}

export type Blocker = {
  key: string
  summary: string
  status: string
  status_key?: string | null
  robot: string | null
  created_at: string | null
  hours_created: string | null
  url: string
  bucket: string
  priority?: string | null
  assignee?: { display: string; login?: string } | null
}

export type MechanicTasks = {
  park_tag: string
  status: string
  counts: Record<string, number>
  items: Blocker[]
}

export type OperationsFlow = {
  definition_version: 2
  window_start: string
  window_end: string
  expected_buckets: number
  observed_buckets: number
  complete: boolean
  legacy_buckets: number
  points: { bucket_start: string; arrived_count: number; departed_count: number }[]
}
export type OperationsSlaPolicy = { park_id: number; target_hours: number | null }
export type OperationsOverview = {
  park_id: number
  generated_at: string
  timezone: 'Europe/Moscow'
  status_options: { key: string; label: string }[]
  selected_status: string
  counts: Record<string, number>
  tasks: Blocker[]
  tasks_total: number
  tasks_truncated: boolean
  flow: OperationsFlow
  sla: {
    target_hours: number | null
    evaluated_count: number
    unknown_count: number
    at_risk_count: number | null
    overdue_count: number | null
    overdue: (Blocker & { age_hours: number; overdue_hours: number })[]
    overdue_truncated: boolean
  }
  workload: { login: string | null; display: string; open_count: number; overdue_count: number | null; oldest_hours: number | null }[] | null
  operators: { user_id: number; username: string; tracker_login: string | null; open_count: number | null; overdue_count: number | null; oldest_hours: number | null }[] | null
}

export type OperatorBlockers = {
  park_id: number
  park_tag: string
  status: string
  counts: Record<string, number>
  items: Blocker[]
}

export type NowReport = {
  generated_at: string
  scope: string
  totals: Record<string, number>
  parks: Array<{
    park_id: number
    park_name: string
    park_tag: string
    metrics: Record<string, number>
  }>
  skipped_parks: Array<{ park_id: number; park_name: string; reason: string }>
}

export type EmergencySection = {
  id: string
  title: string
}

export type EmergencySectionDetail = {
  id: string
  title: string
  fields: { label: string; lines: string[] }[]
}

export type EmergencySnapshot = {
  vin: string
  short_number: string
  observed_at: string
  stale?: boolean
  stale_age_seconds?: number
  online: boolean | null
  speed: number | null
  charge_percent: number | null
  battery1_percent: number | null
  battery2_percent: number | null
  battery1_connected?: boolean | null
  battery2_connected?: boolean | null
  disk_percent: number | null
  mode: string | null
  icp_label: string | null
  icp_ok: boolean | null
  lte_label: string | null
  lte_ok: boolean | null
  connection: 'lte' | 'wire' | null
  sim_signals?: number[]
  error_banner: string | null
  lat: number | null
  lon: number | null
  heading_deg: number | null
  wheels_fault: string[]
  diagnostic_events?: DiagnosticEvent[]
  readings?: EmergencyReadingValue[]
}

export type EmergencyViewerRole = 'mechanic' | 'operator' | 'admin' | 'royal' | 'driver'

export type EmergencyAdminField = {
  id: number
  path: string
  label: string
  sort_order: number
}

export type EmergencyAdminSection = {
  id: string
  title: string
  sort_order: number
  is_enabled: boolean
  formatter: string | null
  meta: Record<string, unknown> | null
  roles: EmergencyViewerRole[]
  fields: EmergencyAdminField[]
}

export type EmergencySectionCreate = {
  id: string
  title: string
  is_enabled?: boolean
  formatter?: string | null
  roles?: EmergencyViewerRole[]
  fields?: Array<{ path: string; label: string }>
}

export type TrackerPerson = {
  display: string
  login?: string
}

export type TrackerIssue = {
  key: string
  summary: string
  status: string
  status_key?: string | null
  queue?: string | null
  robot?: string | null
  created_at?: string | null
  updated_at?: string | null
  hours_created?: string | null
  url: string
  tags?: string[]
  priority?: string | null
  type?: string | null
  assignee?: TrackerPerson | null
  queued_at?: string | null
  sla_deadline?: string | null
  sla_source?: 'status_history' | 'estimated' | null
}

export type Paged<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
  has_more: boolean
}

export type AuditEntry = {
  id: number
  action: string
  actor_user_id: number | null
  actor_username: string | null
  actor_role: string | null
  park_id: number | null
  target_type: string | null
  target_id: string | null
  outcome: 'success' | 'failure' | 'denied' | string
  detail: string | null
  client_ip: string | null
  created_at: string
}

export type TrackerIssueCapabilities = {
  comment: boolean
  assign: boolean
  unassign: boolean
  transition: boolean
  close: boolean
  attach: boolean
}

export type TaskSyncState = 'saved' | 'pending' | 'synced' | 'needs_attention'
export type TaskWorkflow = {
  owner: TrackerPerson | null
  review_state: 'pending' | 'returned' | 'closed' | null
  display_status: 'queued' | 'in_progress' | 'review' | 'closed' | 'hidden'
  sync_state: TaskSyncState
  queued_at?: string | null
  queued_at_source?: 'tracker_history' | 'created_at_estimate' | null
  hidden?: { reason: string; actor: string; created_at: string } | null
  has_current_cycle_comment: boolean
}

export type TrackerIssueDetail = TrackerIssue & {
  resolution?: string | null
  description?: string | null
  reporter?: TrackerPerson | null
  components?: string[]
  attachments?: TrackerAttachment[]
  claim?: { park_id: number } | null
  capabilities: TrackerIssueCapabilities
  workflow?: TaskWorkflow
}

export type TrackerAttachment = {
  id: string
  name: string
  size?: number | null
  url?: string | null
  mimetype?: string | null
}

export type TrackerUserSuggestion = {
  login: string
  display: string
  source: string
}
export type TrackerComment = {
  id: string
  text: string
  author?: string | null
  author_login?: string | null
  created_at?: string | null
  attachments?: TrackerAttachment[]
}
export type TrackerTransition = { id: string; display: string }
export type TrackerActionResult = { key: string; action: string; status: string; actor: string; performed_at: string }
export type TaskActionResult = TrackerActionResult & { sync_state: TaskSyncState; workflow: TaskWorkflow | null }
export type TaskTimelineItem = {
  id: string; kind: 'user' | 'system' | 'tracker'; author: string; text: string
  created_at: string; sync_state: TaskSyncState; attachments: TrackerAttachment[]
}
export type TaskAttachmentStaged = {
  id: string; message_id: string; name: string; mimetype: string; size: number
  sha256: string; action_id: string; sync_state: 'pending' | 'needs_attention'
}
export type DefectCode = { code: string; label: string; description: string | null }

export type DashboardMovingItem = {
  key: string
  summary: string
}

export type DashboardSummary = {
  park_id: number
  generated_at: string
  arrived: number
  done: number
  queued: number
  in_transit: number
  moving: DashboardMovingItem[]
}

export type DashboardHistoryPoint = {
  bucket_start: string
  arrived_count: number
  departed_count: number
}

export type DashboardHistory = {
  park_id: number
  points: DashboardHistoryPoint[]
}

export type ReportKindManual = 'ticket_question' | 'mechanic_problem'

export type ReportAttachmentKind = 'ui_snapshot' | 'device_photo' | 'client_log'

export type ReportAttachment = {
  id: number
  kind: ReportAttachmentKind | string
  filename: string
  content_type: string
  size_bytes: number
}

export type Report = {
  id: number
  kind: string
  status: string
  park_id: number | null
  author_user_id: number
  target_role: string
  tracker_key: string | null
  tracker_url: string | null
  title: string
  body: string
  parent_report_id: number | null
  return_comment: string | null
  created_at: string
  updated_at: string
  resolved_at: string | null
  attachments?: ReportAttachment[]
}

export type ReportBadge = {
  count: number
}

export type ReportCreatePayload = {
  kind: ReportKindManual
  park_id: number
  title: string
  body?: string
  tracker_key?: string | null
  tracker_url?: string | null
}

export type CampaignKind = 'service_company' | 'wrapping'

export type CampaignTicket = {
  key: string
  summary: string
  status: string
  park_id: number
  park_name: string
  robot: string | null
  url: string
  completed_at: string | null
  completed_by: number | null
  comment: string | null
  report_id: number | null
  review_status: string | null
  tracker_transition: string | null
}

export type Campaign = {
  id: number
  kind: CampaignKind
  name: string
  tracker_tag: string
  starts_on: string
  due_on: string
  is_active: boolean
  park_ids: number[]
  park_names: string[]
  total_count: number
  completed_count: number
  pending_review_count: number
  remaining_count: number
  percent_complete: number
  overdue: boolean
  snapshot_at?: string | null
  snapshot_state?: 'idle' | 'pending' | 'running' | 'ready' | 'error'
  snapshot_error?: string | null
}

export type CampaignDetail = Campaign & {
  open_tickets: CampaignTicket[]
  closed_tickets: CampaignTicket[]
}

export type CampaignCreatePayload = Pick<Campaign, 'kind' | 'name' | 'tracker_tag' | 'starts_on' | 'due_on' | 'park_ids'>

export type CampaignSubmission = {
  id: number
  issue_key: string
  report_id: number
  review_status: string
  tracker_transition: string | null
  completed_at: string
}

export type InventoryPart = {
  id: number; park_id: number; component_id: number; name: string; article: string
  quantity: InventoryInt64; minimum_quantity: InventoryInt64; location: string; is_active: boolean; has_photo: boolean
}
export type InventoryComponent = { id: number; park_id: number; name: string; has_photo: boolean; parts: InventoryPart[] }
export type InventoryOverview = { park_id: number; component_count: number; part_count: number; low_stock_count: number; out_of_stock_count: number; components: InventoryComponent[] }
export type InventoryMovement = { id: number; part_id: number; park_id: number; actor_user_id: number; actor_username: string; kind: string; delta: InventoryInt64; balance_after: InventoryInt64; issue_key: string | null; note: string | null; created_at: string }

export type HostCheck = {
  code: string
  status: 'ok' | 'warning' | 'failed'
  message: string
  repair?: string | null
}

export type SystemHealth = {
  version: string | null
  git_sha: string | null
  generated_at: string | null
  overall: 'ok' | 'degraded' | 'unknown'
  checks: HostCheck[]
  update: {
    state: 'idle' | 'updating' | 'current_healthy' | 'rolled_back' | 'maintenance' | 'unknown'
    publication: 'degraded' | null
  }
  last_backup: { status: 'success' | 'failed' | 'unknown'; completed_at: string | null }
}

export type AvailableUpdate = {
  state: 'available' | 'up_to_date' | 'discovery_stale' | 'disabled' | 'approved'
  checked_at: string | null
  release: { release_id: number; version: string; git_sha: string; size: number; sha256: string } | null
}

export type UpdateInspection = {
  inspection_id: string
  version: string
  git_sha: string
  migration_head: string
  notes: string
}

export type OpsJob = {
  id: string
  kind: 'snapshot' | 'restore' | 'update' | string
  state: 'queued' | 'running' | 'succeeded' | 'failed' | string
  phase: string
  log: string
  error: string | null
  artifact_ready: boolean
  restart_required: boolean
  created_at: string
  updated_at: string
  restore_phrase: string
  update_phrase: string
  progress_percent: number | null
  progress_phase: string | null
  host_result?: { before: HostCheck[]; after: HostCheck[]; performed: string[]; failed: string[] } | null
}

export type OpsMaintenance = {
  active: boolean
  kind: string | null
  operator: boolean
}

export class ApiError extends Error {
  status: number
  detail: string | null
  structuredDetail: Record<string, unknown> | unknown[] | null
  requestId?: string
  retryAfterMs?: number

  constructor(status: number, detail: unknown = null, requestId?: string, retryAfterMs?: number) {
    const stringDetail = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map(String).join('; ')
        : null
    super(stringDetail ?? String(status))
    this.name = 'ApiError'
    this.status = status
    this.detail = stringDetail
    this.structuredDetail = detail !== null && typeof detail === 'object'
      ? detail as Record<string, unknown> | unknown[]
      : null
    this.retryAfterMs = retryAfterMs
    this.requestId = requestId
  }
}

export type DiagnosticView = 'top' | 'front' | 'rear' | 'left' | 'right' | 'isometric'
export type DiagnosticSeverity = 'info' | 'warning' | 'critical'
export type DiagnosticIndicator = 'point' | 'outline' | 'zone'
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }
export type DiagnosticRuleCreate = {
  source_path: string; match_kind: 'exact' | 'regex'; pattern: string; example: string
  title: string; description: string; severity: DiagnosticSeverity; part: string
  preferred_view: DiagnosticView; x: number; y: number; indicator: DiagnosticIndicator
  is_enabled?: boolean; sort_order?: number
}
export type DiagnosticRule = Required<DiagnosticRuleCreate> & { id: number }
export type DiagnosticRuleUpdate = Partial<Omit<DiagnosticRuleCreate, 'sort_order'>>
export type DiagnosticCatalog = { rules: DiagnosticRule[]; etag: string | null }
export type DiagnosticEvent = {
  id: string; rule_id: number | null; source_path: string; source_segments: (string | number)[]
  raw_value: JsonValue; title: string; description: string; severity: DiagnosticSeverity
  sort_order: number; part: string | null; view: DiagnosticView | null
  x: number | null; y: number | null; indicator: DiagnosticIndicator | null
}
export type DiagnosticPreview = { matched: boolean; events: DiagnosticEvent[] }

export type ReadingState = 'normal' | 'warning' | 'critical' | 'unavailable'
export type EmergencyReadingValue = {
  id: number
  section_id: string
  label: string
  display: string
  state: ReadingState
  view: DiagnosticView
  x: number
  y: number
  label_direction: EmergencyReadingLabelDirection
}
export type EmergencyReadingDisplayKind = 'text' | 'number' | 'percent' | 'distance' | 'current' | 'state'
export type EmergencyReadingLabelDirection = 'auto' | 'left' | 'right' | 'top' | 'bottom'
export type EmergencyReadingDraft = {
  section_id: string
  path: string
  label: string
  display_kind: EmergencyReadingDisplayKind
  unit: string | null
  precision: number
  enabled_path: string | null
  no_data_values: JsonValue[]
  warning_below: number | null
  warning_above: number | null
  critical_below: number | null
  critical_above: number | null
  view: DiagnosticView
  x: number
  y: number
  label_direction: EmergencyReadingLabelDirection
  is_enabled: boolean
  sort_order: number
}
export type EmergencyReading = EmergencyReadingDraft & { id: number }
export type EmergencyDiscoveredField = {
  path: string
  value_type: 'string' | 'number' | 'boolean' | 'null'
  example: string
}
export type EmergencyReadingUpdate = Partial<Omit<EmergencyReadingDraft, 'sort_order'>>
export type EmergencyReadingCatalog = { readings: EmergencyReading[]; etag: string | null }

// Keep only fixed backend codes. Validation bodies may contain sensitive examples.
const diagnosticErrorCodes = new Set([
  'invalid_diagnostic_source_path', 'invalid_diagnostic_regex', 'unsupported_diagnostic_regex',
  'diagnostic_preview_source_too_large', 'invalid_diagnostic_rule', 'diagnostic_rule_conflict',
  'diagnostic_rules_write_conflict', 'diagnostic_rules_changed', 'diagnostic_rules_precondition_required',
])

async function diagnosticRequest<T>(suffix = '', init: RequestInit = {}): Promise<{ data: T; etag: string | null }> {
  return fetchWithTimeout(`/api/admin/diagnostic-rules${suffix}`, {
    ...init, credentials: 'include', headers: { 'Content-Type': 'application/json', ...init.headers },
  }, JSON_TIMEOUT_MS, async response => {
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: unknown } | null
      const detail = typeof body?.detail === 'string' && diagnosticErrorCodes.has(body.detail) ? body.detail : null
      throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
    }
    return { data: await response.json() as T, etag: response.headers.get('ETag') }
  })
}

const emergencyReadingErrorCodes = new Set([
  'emergency_reading_conflict', 'emergency_reading_not_found', 'emergency_readings_write_conflict',
  'emergency_readings_catalog_changed', 'emergency_readings_if_match_required',
  'invalid_emergency_reading', 'invalid_emergency_reading_path', 'emergency_section_not_found',
  'invalid_robot_number', 'emergency_cookie_not_configured', 'emergency_cookie_invalid',
  'emergency_upstream_error',
])

async function emergencyReadingRequest<T>(suffix = '', init: RequestInit = {}): Promise<{ data: T; etag: string | null }> {
  return fetchWithTimeout(`/api/admin/emergency-readings${suffix}`, {
    ...init,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init.headers },
  }, JSON_TIMEOUT_MS, async response => {
    if (!response.ok) {
      const body = await response.json().catch(() => null) as { detail?: unknown } | null
      const detail = typeof body?.detail === 'string' && emergencyReadingErrorCodes.has(body.detail)
        ? body.detail
        : null
      throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
    }
    return {
      data: (response.status === 204 ? undefined : await response.json()) as T,
      etag: response.headers.get('ETag'),
    }
  })
}

function emergencyReadingChanges(reading: Partial<EmergencyReadingDraft>): Partial<EmergencyReadingDraft> {
  const {
    section_id, path, label, display_kind, unit, precision, enabled_path, no_data_values,
    warning_below, warning_above, critical_below, critical_above, view, x, y,
    label_direction, is_enabled,
  } = reading
  return {
    section_id, path, label, display_kind, unit, precision, enabled_path, no_data_values,
    warning_below, warning_above, critical_below, critical_above, view, x, y,
    label_direction, is_enabled,
  }
}

function emergencyReadingDraft(reading: EmergencyReadingDraft): EmergencyReadingDraft {
  return { ...emergencyReadingChanges(reading) as EmergencyReadingDraft, sort_order: reading.sort_order }
}

function diagnosticChanges(changes: DiagnosticRuleUpdate): DiagnosticRuleUpdate {
  const { source_path, match_kind, pattern, example, title, description, severity, part,
    preferred_view, x, y, indicator, is_enabled } = changes
  return { source_path, match_kind, pattern, example, title, description, severity, part,
    preferred_view, x, y, indicator, is_enabled }
}

export class ApiTimeoutError extends Error {
  readonly timeoutMs: number

  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`)
    this.name = 'ApiTimeoutError'
    this.timeoutMs = timeoutMs
  }
}

const JSON_TIMEOUT_MS = 30_000
const BLOB_TIMEOUT_MS = 60_000
const FORM_TIMEOUT_MS = 90_000

async function fetchWithTimeout<T>(
  input: RequestInfo | URL,
  init: RequestInit,
  timeoutMs: number,
  consume: (response: Response) => Promise<T>,
): Promise<T> {
  const controller = new AbortController()
  const sourceSignal = init.signal
  let timedOut = false
  const forwardAbort = () => controller.abort(sourceSignal?.reason)
  if (sourceSignal?.aborted) forwardAbort()
  else sourceSignal?.addEventListener('abort', forwardAbort, { once: true })
  const timer = globalThis.setTimeout(() => {
    if (controller.signal.aborted) return
    timedOut = true
    controller.abort()
  }, timeoutMs)

  try {
    const response = await fetch(input, { ...init, signal: controller.signal })
    return await consume(response)
  } catch (error) {
    if (timedOut) throw new ApiTimeoutError(timeoutMs)
    if (sourceSignal?.aborted) throw sourceSignal.reason
    throw error
  } finally {
    globalThis.clearTimeout(timer)
    sourceSignal?.removeEventListener('abort', forwardAbort)
  }
}

function responseRetryAfter(response: Response): number | undefined {
  const value = response.headers.get('Retry-After')?.trim()
  if (!value) return undefined
  const delay = /^\d+(?:\.\d+)?$/.test(value) ? Number(value) * 1_000 : Date.parse(value) - Date.now()
  return Number.isFinite(delay) ? Math.max(0, delay) : undefined
}

function responseRequestId(response: Response): string | undefined {
  return response.headers.get('X-Request-ID')?.trim() || undefined
}

async function readErrorDetail(response: Response): Promise<unknown> {
  try {
    const body = await response.json() as { detail?: unknown }
    return body.detail ?? null
  } catch {
    return null
  }
}

const INVENTORY_INT64_FIELDS = new Set([
  'actual_quantity',
  'balance_after',
  'current_quantity',
  'delta',
  'difference',
  'expected_quantity',
  'minimum_quantity',
  'quantity',
  'version',
])

function quoteInventoryInt64Values(text: string): string {
  let result = ''
  let index = 0
  let valueKey: string | null = null

  while (index < text.length) {
    const character = text[index]
    if (character === '"') {
      let end = index + 1
      while (end < text.length) {
        if (text[end] === '\\') end += 2
        else if (text[end] === '"') { end += 1; break }
        else end += 1
      }
      const token = text.slice(index, end)
      let next = end
      while (next < text.length && ' \n\r\t'.includes(text[next])) next += 1
      valueKey = text[next] === ':' ? JSON.parse(token) as string : null
      result += token
      index = end
      continue
    }

    if (character === '-' || (character >= '0' && character <= '9')) {
      let end = index + 1
      while (end < text.length && '0123456789.eE+-'.includes(text[end])) end += 1
      const token = text.slice(index, end)
      result += valueKey && INVENTORY_INT64_FIELDS.has(valueKey) && !token.includes('.') && !token.includes('e') && !token.includes('E')
        ? JSON.stringify(token)
        : token
      valueKey = null
      index = end
      continue
    }

    if (character === ',' || character === '{' || character === '['
      || character === 't' || character === 'f' || character === 'n') valueKey = null
    result += character
    index += 1
  }
  return result
}

function parseInventoryJson<T>(text: string): T {
  return JSON.parse(quoteInventoryInt64Values(text)) as T
}

function inventoryStringify(value: unknown): string {
  return quoteInventoryInt64Values(JSON.stringify(value))
}

async function inventoryRequest<T>(path: string, init?: RequestInit): Promise<T> {
  return fetchWithTimeout(
    `/api${path}`,
    {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      ...init,
    },
    JSON_TIMEOUT_MS,
    async (response) => {
      const text = await response.text()
      if (!response.ok) {
        let detail: unknown = null
        try { detail = parseInventoryJson<{ detail?: unknown }>(text).detail ?? null } catch { /* malformed error body */ }
        throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
      }
      if (response.status === 204) return undefined as T
      return parseInventoryJson<T>(text)
    },
  )
}

async function inventoryFormRequest<T>(path: string, formData: FormData): Promise<T> {
  return fetchWithTimeout(
    `/api${path}`,
    { credentials: 'include', method: 'POST', body: formData },
    FORM_TIMEOUT_MS,
    async (response) => {
      const text = await response.text()
      if (!response.ok) {
        let detail: unknown = null
        try { detail = parseInventoryJson<{ detail?: unknown }>(text).detail ?? null } catch { /* malformed error body */ }
        throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
      }
      return parseInventoryJson<T>(text)
    },
  )
}

export function inventoryErrorDetail(error: unknown): InventoryApiErrorDetail | null {
  if (!(error instanceof ApiError) || !error.structuredDetail || Array.isArray(error.structuredDetail)) return null
  return typeof error.structuredDetail.code === 'string'
    ? error.structuredDetail as InventoryApiErrorDetail
    : null
}

export function isInventoryCountStaleErrorDetail(detail: InventoryApiErrorDetail | null): detail is InventoryCountStaleErrorDetail {
  if (detail?.code !== 'inventory_count_stale' || !Array.isArray(detail.conflicts)) return false
  return detail.conflicts.every(conflict => {
    if (!conflict || typeof conflict !== 'object') return false
    const value = conflict as Record<string, unknown>
    const quantity = (candidate: unknown) => typeof candidate === 'string' && /^(0|[1-9]\d*)$/.test(candidate) && BigInt(candidate) <= 9223372036854775807n
    if (typeof value.catalog_part_id !== 'number' || !Number.isInteger(value.catalog_part_id) || !quantity(value.expected_quantity) || !quantity(value.current_quantity)) return false
    if (value.affected_lines === undefined) return true
    return Array.isArray(value.affected_lines) && value.affected_lines.every(line => {
      if (!line || typeof line !== 'object') return false
      const affected = line as Record<string, unknown>
      return typeof affected.count_line_id === 'number' && Number.isInteger(affected.count_line_id)
        && typeof affected.catalog_part_id === 'number' && Number.isInteger(affected.catalog_part_id)
    })
  })
}

export function isInventoryDuplicateErrorDetail(detail: InventoryApiErrorDetail | null): detail is InventoryDuplicateErrorDetail {
  if (detail?.code === 'inventory_article_exists') return typeof detail.existing_part_id === 'number'
  if (detail?.code === 'inventory_component_exists') return typeof detail.existing_component_id === 'number'
  return false
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return fetchWithTimeout(
    `/api${path}`,
    {
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
      ...init,
    },
    JSON_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) {
        const detail = await readErrorDetail(response)
        throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
      }

      if (response.status === 204) {
        return undefined as T
      }

      return response.json() as Promise<T>
    },
  )
}

async function requestBlob(path: string): Promise<Blob> {
  return fetchWithTimeout(
    `/api${path}`,
    { credentials: 'include' },
    BLOB_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) {
        const detail = await readErrorDetail(response)
        throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
      }
      return response.blob()
    },
  )
}

const INVENTORY_EXPORT_MEDIA_TYPES = {
  csv: 'text/csv',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
} as const

async function downloadInventoryExport(options: InventoryExportParams): Promise<void> {
  const query = new URLSearchParams()
  if ('parkId' in options && options.parkId !== undefined) query.set('park_id', String(options.parkId))
  else query.set('scope', 'all')
  query.set('format', options.format)

  return fetchWithTimeout(
    `/api/inventory/export?${query.toString()}`,
    { credentials: 'include' },
    BLOB_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) {
        const text = await response.text()
        let detail: unknown = null
        try { detail = parseInventoryJson<{ detail?: unknown }>(text).detail ?? null } catch { /* malformed error body */ }
        throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
      }
      const contentType = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase()
      if (contentType !== INVENTORY_EXPORT_MEDIA_TYPES[options.format]) {
        throw new ApiError(502, 'inventory_export_content_type_invalid', responseRequestId(response))
      }

      const objectUrl = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      try {
        anchor.href = objectUrl
        anchor.download = `inventory.${options.format}`
        anchor.hidden = true
        document.body.append(anchor)
        anchor.click()
      } finally {
        anchor.remove()
        URL.revokeObjectURL(objectUrl)
      }
    },
  )
}

function inventoryListQuery(params: InventoryListParams = {}): string {
  const query = new URLSearchParams()
  if (params.query) query.set('q', params.query)
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  const value = query.toString()
  return value ? `?${value}` : ''
}

async function requestForm<T>(path: string, formData: FormData, headers?: Record<string, string>): Promise<T> {
  return fetchWithTimeout(
    `/api${path}`,
    {
      credentials: 'include',
      method: 'POST',
      body: formData,
      headers,
    },
    FORM_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) {
        const detail = await readErrorDetail(response)
        throw new ApiError(response.status, detail, responseRequestId(response), responseRetryAfter(response))
      }

      return response.json() as Promise<T>
    },
  )
}

export const api = {
  changeRevision: (scope: string) => request<{ revision: number }>(`/changes?scope=${encodeURIComponent(scope)}`),
  emergencyReadings: async (signal?: AbortSignal): Promise<EmergencyReadingCatalog> => {
    const result = await emergencyReadingRequest<EmergencyReading[]>('', { signal })
    return { readings: result.data, etag: result.etag }
  },
  discoverEmergencyReadings: async (vin: string, signal?: AbortSignal) => (
    await emergencyReadingRequest<EmergencyDiscoveredField[]>(`/discovered?${new URLSearchParams({ vin })}`, { signal })
  ).data,
  createEmergencyReading: async (reading: EmergencyReadingDraft) => (
    await emergencyReadingRequest<EmergencyReading>('', {
      method: 'POST', body: JSON.stringify(emergencyReadingDraft(reading)),
    })
  ).data,
  updateEmergencyReading: async (id: number, changes: EmergencyReadingUpdate) => (
    await emergencyReadingRequest<EmergencyReading>(`/${id}`, {
      method: 'PATCH', body: JSON.stringify(emergencyReadingChanges(changes)),
    })
  ).data,
  disableEmergencyReading: async (id: number) => (
    await emergencyReadingRequest<EmergencyReading>(`/${id}`, {
      method: 'PATCH', body: JSON.stringify({ is_enabled: false }),
    })
  ).data,
  reorderEmergencyReadings: async (ids: number[], etag: string): Promise<EmergencyReadingCatalog> => {
    const result = await emergencyReadingRequest<EmergencyReading[]>('/reorder', {
      method: 'PUT', headers: { 'If-Match': etag }, body: JSON.stringify({ ids }),
    })
    return { readings: result.data, etag: result.etag }
  },
  deleteEmergencyReading: async (id: number) => {
    await emergencyReadingRequest<void>(`/${id}`, { method: 'DELETE' })
  },
  diagnosticRules: async (signal?: AbortSignal): Promise<DiagnosticCatalog> => {
    const result = await diagnosticRequest<DiagnosticRule[]>('', { signal })
    return { rules: result.data, etag: result.etag }
  },
  createDiagnosticRule: async (rule: DiagnosticRuleCreate) =>
    (await diagnosticRequest<DiagnosticRule>('', { method: 'POST', body: JSON.stringify(rule) })).data,
  updateDiagnosticRule: async (id: number, changes: DiagnosticRuleUpdate) =>
    (await diagnosticRequest<DiagnosticRule>(`/${id}`, { method: 'PATCH', body: JSON.stringify(diagnosticChanges(changes)) })).data,
  disableDiagnosticRule: async (id: number) =>
    (await diagnosticRequest<DiagnosticRule>(`/${id}/disable`, { method: 'POST' })).data,
  reorderDiagnosticRules: async (ids: number[], etag: string): Promise<DiagnosticCatalog> => {
    const result = await diagnosticRequest<DiagnosticRule[]>('/reorder', {
      method: 'PUT', headers: { 'If-Match': etag }, body: JSON.stringify({ ids }),
    })
    return { rules: result.data, etag: result.etag }
  },
  previewDiagnosticRule: async (rule: DiagnosticRuleCreate, payload?: Record<string, JsonValue>, signal?: AbortSignal) =>
    (await diagnosticRequest<DiagnosticPreview>('/preview', { method: 'POST', body: JSON.stringify({ rule, payload }), signal })).data,
  me: () => request<User>('/auth/me'),
  login: (username: string, password: string, rememberMe = false) =>
    request<void>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, remember_me: rememberMe }),
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  changePassword: (current_password: string, new_password: string) =>
    request<void>('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    }),
  register: (shared_password: string, username: string, password: string, role_slug: string) =>
    request<User>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ shared_password, username, password, role_slug }),
    }),
  parks: () => request<Park[]>('/parks'),
  createPark: (payload: {
    name: string
    tag: string
    tracker_queue?: string | null
    tracker_priority?: string | null
    tracker_type?: string | null
    group_id?: number | null
    chat_id?: number | null
    feature_reports?: boolean
    feature_blockers?: boolean
    feature_sla_repair?: boolean
    feature_backlog_alerts?: boolean
  }) =>
    request<Park>('/parks', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updatePark: (parkId: number, changes: Partial<Park>) =>
    request<Park>(`/parks/${parkId}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    }),
  integrationSettings: () => request<IntegrationSettings>('/admin/settings/integrations'),
  setTrackerToken: (token: string) =>
    request<IntegrationSettings>('/admin/settings/tracker-token', {
      method: 'PUT',
      body: JSON.stringify({ token }),
    }),
  setEmergencyCookie: (cookie: string) =>
    request<IntegrationSettings>('/admin/settings/emergency-cookie', {
      method: 'PUT',
      body: JSON.stringify({ cookie }),
    }),
  checkEmergencyCookie: (robotNumber?: string) =>
    request<IntegrationSettings>('/admin/settings/emergency-cookie/check', {
      method: 'POST',
      body: JSON.stringify(robotNumber ? { robot_number: robotNumber } : {}),
    }),
  trackerPolicy: () => request<TrackerPolicySettings>('/admin/settings/tracker-policy'),
  updateTrackerPolicy: (payload: Partial<TrackerPolicySettings>) =>
    request<TrackerPolicySettings>('/admin/settings/tracker-policy', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  screenshotGuardSettings: () =>
    request<ScreenshotGuardSettings>('/admin/settings/screenshot-guard'),
  updateScreenshotGuardSettings: (payload: Partial<ScreenshotGuardSettings>) =>
    request<ScreenshotGuardSettings>('/admin/settings/screenshot-guard', {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),
  registrationPasswordSettings: () =>
    request<RegistrationPasswordSettings>('/admin/settings/registration-password'),
  setRegistrationPassword: (password: string) =>
    request<RegistrationPasswordSettings>('/admin/settings/registration-password', {
      method: 'PUT',
      body: JSON.stringify({ password }),
    }),
  clearRegistrationPassword: () =>
    request<RegistrationPasswordSettings>('/admin/settings/registration-password', {
      method: 'DELETE',
    }),
  adminRoles: () => request<AdminRole[]>('/admin/roles'),
  adminRolePermissionCatalog: () =>
    request<PermissionCatalogItem[]>('/admin/roles/permissions/catalog'),
  createAdminRole: (payload: {
    slug: string
    name: string
    description?: string
    permissions: string[]
  }) =>
    request<AdminRole>('/admin/roles', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateAdminRole: (
    roleId: number,
    payload: Partial<{
      name: string
      description: string
      is_active: boolean
      permissions: string[]
    }>,
  ) =>
    request<AdminRole>(`/admin/roles/${roleId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteAdminRole: (roleId: number) =>
    request<void>(`/admin/roles/${roleId}`, { method: 'DELETE' }),
  adminUsers: (params?: { role?: string; access_status?: string }) => {
    const query = new URLSearchParams()
    if (params?.role) query.set('role', params.role)
    if (params?.access_status) query.set('access_status', params.access_status)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return request<AdminUser[]>(`/admin/users${suffix}`)
  },
  createAdminUser: (payload: {
    username: string
    password: string
    role_slug: string
    park_ids?: number[]
    tracker_login?: string | null
  }) =>
    request<AdminUser>('/admin/users', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateAdminUser: (
    userId: number,
    payload: Partial<{
      password: string
      role_slug: string
      park_ids: number[]
      is_active: boolean
      tracker_login: string | null
      must_change_password: boolean
      access_status: string
      permissions: string[]
    }>,
  ) =>
    request<AdminUser>(`/admin/users/${userId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  approveAdminUser: (userId: number, parkIds: number[] = []) =>
    request<void>(`/admin/users/${userId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ park_ids: parkIds }),
    }),
  rejectAdminUser: (userId: number) =>
    request<void>(`/admin/users/${userId}/reject`, { method: 'POST' }),
  deleteAdminUser: (userId: number) =>
    request<void>(`/admin/users/${userId}`, { method: 'DELETE' }),
  operatorParks: () => request<Park[]>('/operator/parks'),
  availableParks: () => request<Park[]>('/operator/available-parks'),
  operatorParkRequests: () => request<ParkRequest[]>('/operator/park-requests'),
  requestPark: (parkId: number) =>
    request<ParkRequest>('/operator/park-requests', {
      method: 'POST',
      body: JSON.stringify({ park_id: parkId }),
    }),
  adminParkRequests: () =>
    request<ParkRequest[]>('/admin/park-requests'),
  resolveParkRequest: (requestId: number, resolution: 'approve' | 'reject') =>
    request<void>(`/admin/park-requests/${requestId}/${resolution}`, {
      method: 'POST',
    }),
  mechanicTasks: (status = 'all', parkId?: number) => {
    const params = new URLSearchParams({ status })
    if (parkId != null) params.set('park_id', String(parkId))
    return request<MechanicTasks>(`/mechanic/tasks?${params.toString()}`)
  },
  robotRegistry: (filters: RobotRegistryParams) => {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => { if (value !== undefined) params.set(key, String(value)) })
    return request<RobotRegistry>(`/robots?${params}`)
  },
  mechanicRobotTickets: (query: string) =>
    request<{ query: string; items: Blocker[] }>(
      `/mechanic/robots/${encodeURIComponent(query)}/tickets`,
    ),
  trackerRobotTickets: (query: string) =>
    request<{ query: string; items: Blocker[] }>(
      `/tracker/robots/${encodeURIComponent(query)}/tickets`,
    ),
  mechanicEmergencyResolve: (robot_number: string) =>
    request<{ vin: string; sections: EmergencySection[] }>('/mechanic/emergency/resolve', {
      method: 'POST',
      body: JSON.stringify({ robot_number }),
    }),
  mechanicEmergencySection: (vin: string, sectionId: string) =>
    request<EmergencySectionDetail>(
      `/mechanic/emergency/${encodeURIComponent(vin)}/sections/${encodeURIComponent(sectionId)}`,
    ),
  emergencyResolve: (robot_number: string) =>
    request<{ vin: string; sections: EmergencySection[] }>('/emergency/resolve', {
      method: 'POST',
      body: JSON.stringify({ robot_number }),
    }),
  emergencySnapshot: (vin: string) =>
    request<EmergencySnapshot>(`/emergency/${encodeURIComponent(vin)}/snapshot`),
  emergencySection: (vin: string, sectionId: string) =>
    request<EmergencySectionDetail>(
      `/emergency/${encodeURIComponent(vin)}/sections/${encodeURIComponent(sectionId)}`,
    ),
  adminEmergencySections: () =>
    request<EmergencyAdminSection[]>('/admin/emergency/sections'),
  createEmergencySection: (payload: EmergencySectionCreate) =>
    request<EmergencyAdminSection>('/admin/emergency/sections', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  updateEmergencySection: (
    sectionId: string,
    changes: Partial<Pick<
      EmergencyAdminSection,
      'title' | 'is_enabled' | 'formatter' | 'meta' | 'roles'
    >>,
  ) =>
    request<EmergencyAdminSection>(
      `/admin/emergency/sections/${encodeURIComponent(sectionId)}`,
      { method: 'PATCH', body: JSON.stringify(changes) },
    ),
  deleteEmergencySection: (sectionId: string) =>
    request<void>(`/admin/emergency/sections/${encodeURIComponent(sectionId)}`, {
      method: 'DELETE',
    }),
  reorderEmergencySections: (ids: string[]) =>
    request<EmergencyAdminSection[]>('/admin/emergency/sections/reorder', {
      method: 'PUT',
      body: JSON.stringify({ ids }),
    }),
  createEmergencyField: (sectionId: string, payload: { path: string; label: string }) =>
    request<EmergencyAdminField>(
      `/admin/emergency/sections/${encodeURIComponent(sectionId)}/fields`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  updateEmergencyField: (
    fieldId: number,
    changes: Partial<Pick<EmergencyAdminField, 'path' | 'label'>>,
  ) =>
    request<EmergencyAdminField>(`/admin/emergency/fields/${fieldId}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    }),
  deleteEmergencyField: (fieldId: number) =>
    request<void>(`/admin/emergency/fields/${fieldId}`, { method: 'DELETE' }),
  exportEmergencyConfig: () => requestBlob('/admin/emergency/export'),
  trackerIssues: (params: {
    queue?: string
    park?: string
    status?: string
    robot?: string
    related_repairs?: boolean
    open_only?: boolean
    robot_exact?: string
    exclude_key?: string
    assignee?: string
    untagged?: boolean
    age_hours?: number
    sort?: 'oldest' | 'newest'
    limit?: number
    offset?: number
    owned_by_me?: boolean
    include_hidden?: boolean
  }) => {
    const q = new URLSearchParams({ sort: params.sort ?? 'oldest' })
    Object.entries(params).forEach(([key, value]) => {
      if (key === 'sort') return
      if (value !== undefined && value !== null && value !== '') {
        q.set(key, String(value))
      }
    })
    return request<Paged<TrackerIssue>>(`/tracker/issues?${q.toString()}`)
  },
  trackerUsers: (q: string) =>
    request<TrackerUserSuggestion[]>(`/tracker/users?q=${encodeURIComponent(q)}`),
  auditLog: (params: {
    action?: string
    actor_user_id?: number
    target_id?: string
    park_id?: number
    limit?: number
    offset?: number
  } = {}) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        q.set(key, String(value))
      }
    })
    return request<Paged<AuditEntry>>(`/admin/audit?${q.toString()}`)
  },
  auditActions: () => request<string[]>('/admin/audit/actions'),
  trackerIssue: (key: string, signal?: AbortSignal, includeHidden = false) =>
    request<TrackerIssueDetail>(
      `/tracker/issues/${encodeURIComponent(key)}${includeHidden ? '?include_hidden=true' : ''}`,
      signal ? { signal } : undefined,
    ),
  trackerComments: (key: string) =>
    request<TrackerComment[]>(`/tracker/issues/${encodeURIComponent(key)}/comments`),
  taskTimeline: (key: string) =>
    request<TaskTimelineItem[]>(`/tracker/issues/${encodeURIComponent(key)}/timeline`),
  taskDefectCodes: () => request<DefectCode[]>('/tracker/defect-codes'),
  taskMessage: (key: string, text: string, idempotencyKey: string) =>
    request<TaskTimelineItem>(`/tracker/issues/${encodeURIComponent(key)}/messages`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ text }),
    }),
  taskMessageAttachment: (key: string, messageId: string, file: File, idempotencyKey: string) => {
    const form = new FormData(); form.append('message_id', messageId); form.append('file', file, file.name)
    return requestForm<TaskAttachmentStaged>(`/tracker/issues/${encodeURIComponent(key)}/message-attachments`, form, { 'Idempotency-Key': idempotencyKey })
  },
  taskPhoto: (key: string, file: File, idempotencyKey: string) => {
    const form = new FormData(); form.append('file', file, file.name)
    return requestForm<TaskAttachmentStaged>(`/tracker/issues/${encodeURIComponent(key)}/photos`, form, { 'Idempotency-Key': idempotencyKey })
  },
  taskClaim: (key: string, idempotencyKey: string) => request<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/claim`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } }),
  taskHandoff: (key: string, value: { assignee: string; reason: string; done?: string; remaining?: string; obstacles?: string }, idempotencyKey: string) => request<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/handoff`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey }, body: JSON.stringify(value) }),
  taskSubmitReview: (key: string, value: { defectCode: string; photo: File; comment?: string }, idempotencyKey: string) => {
    const form = new FormData(); form.append('defect_code', value.defectCode); form.append('photo', value.photo, value.photo.name); if (value.comment?.trim()) form.append('comment', value.comment.trim())
    return requestForm<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/submit-review`, form, { 'Idempotency-Key': idempotencyKey })
  },
  taskReturnReview: (key: string, reason: string, assignee: string | undefined, idempotencyKey: string) => request<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/review/return`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ reason, assignee }) }),
  taskApproveReview: (key: string, idempotencyKey: string) => request<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/review/approve`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } }),
  taskRetryNow: (key: string, idempotencyKey: string) => request<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/retry-now`, { method: 'POST', headers: { 'Idempotency-Key': idempotencyKey } }),
  taskHide: (key: string, reason: string, idempotencyKey: string) => request<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/hide`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey }, body: JSON.stringify({ reason }) }),
  taskRestore: (key: string, idempotencyKey: string) => request<TaskActionResult>(`/tracker/issues/${encodeURIComponent(key)}/hide`, { method: 'DELETE', headers: { 'Idempotency-Key': idempotencyKey } }),
  trackerTransitions: (key: string) =>
    request<TrackerTransition[]>(`/tracker/transitions/${encodeURIComponent(key)}`),
  trackerComment: (key: string, text: string, headers?: Record<string, string>) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/comment`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify({ text }),
    }),
  trackerAttach: (key: string, file: File, headers?: Record<string, string>) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return requestForm<TrackerActionResult>(
      `/tracker/issues/${encodeURIComponent(key)}/attachments`,
      form,
      headers,
    )
  },
  trackerAssign: (key: string, assignee: string, headers?: Record<string, string>) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/assign`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify({ assignee }),
    }),
  trackerUnassign: (key: string, headers?: Record<string, string>) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/unassign`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
    }),
  trackerTransition: (key: string, transition: string, resolution?: string, headers?: Record<string, string>) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/transition`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify({ transition, resolution }),
    }),
  trackerClose: (key: string, headers?: Record<string, string>) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/close`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
    }),
  operatorBlockers: (parkId: number, status = 'all') =>
    request<OperatorBlockers>(
      `/operator/blockers?park_id=${parkId}&status=${encodeURIComponent(status)}`,
    ),
  operatorRobotTickets: (query: string) =>
    request<{ query: string; items: Blocker[] }>(
      `/operator/robots/${encodeURIComponent(query)}/tickets`,
    ),
  operatorNowReport: (parkId?: number) =>
    request<NowReport>(
      parkId == null
        ? '/operator/now-report'
        : `/operator/now-report?park_id=${parkId}`,
    ),
  dashboardSummary: (parkId: number) =>
    request<DashboardSummary>(`/dashboard/summary?park_id=${parkId}`),
  operationsOverview: (parkId: number, days = 7, status = 'all') =>
    request<OperationsOverview>(`/operations/overview?${new URLSearchParams({ park_id: String(parkId), days: String(days), status })}`),
  analytics: (parkId: number, days = 7, bucket: AnalyticsBucket = '1d') =>
    request<HistoricalAnalytics>(`/analytics?${new URLSearchParams({ park_id: String(parkId), days: String(days), bucket })}`),
  operationsSlaPolicy: (parkId: number) =>
    request<OperationsSlaPolicy>(`/operations/sla-policy?park_id=${parkId}`),
  updateOperationsSlaPolicy: (parkId: number, body: { target_hours: number | null }) =>
    request<OperationsSlaPolicy>(`/operations/sla-policy?park_id=${parkId}`, { method: 'PUT', body: JSON.stringify(body) }),
  dashboardHistory: (parkId: number, days = 7) =>
    request<DashboardHistory>(
      `/dashboard/history?park_id=${parkId}&days=${days}`,
    ),
  campaigns: (parkId?: number) =>
    request<Campaign[]>(parkId == null ? '/campaigns' : `/campaigns?park_id=${parkId}`),
  campaign: (id: number) => request<CampaignDetail>(`/campaigns/${id}`),
  refreshCampaign: (id: number) => request<{ snapshot_state: string; snapshot_at: string | null }>(`/campaigns/${id}/refresh`, { method: 'POST' }),
  deleteCampaign: (id: number) => request<{ result: 'deleted' | 'archived' }>(`/campaigns/${id}`, { method: 'DELETE' }),
  createCampaign: (payload: CampaignCreatePayload) =>
    request<Campaign>('/campaigns', { method: 'POST', body: JSON.stringify(payload) }),
  updateCampaign: (id: number, payload: Partial<CampaignCreatePayload & { is_active: boolean }>) =>
    request<Campaign>(`/campaigns/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  completeCampaignTicket: (campaignId: number, ticketKey: string, parkId: number, comment: string, photo: File, idempotencyKey?: string) => {
    const form = new FormData()
    form.append('park_id', String(parkId))
    form.append('comment', comment)
    form.append('photo', photo, photo.name)
    return requestForm<CampaignSubmission>(
      `/campaigns/${campaignId}/tickets/${encodeURIComponent(ticketKey)}/complete`,
      form,
      idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    )
  },
  inventory: (parkId: number) => inventoryRequest<InventoryOverview>(`/inventory?park_id=${parkId}`),
  searchInventory: ({ parkId, query: search, componentId, stockFilter, mode, limit, offset }: InventorySearchParams) => {
    const query = new URLSearchParams({ park_id: String(parkId) })
    if (search) query.set('q', search)
    if (componentId !== undefined) query.set('component_id', String(componentId))
    if (stockFilter) query.set('stock_filter', stockFilter)
    if (mode) query.set('mode', mode)
    if (limit !== undefined) query.set('limit', String(limit))
    if (offset !== undefined) query.set('offset', String(offset))
    return inventoryRequest<InventoryPageEnvelope<InventoryCatalogSearchItem>>(`/inventory/catalog/search?${query.toString()}`)
  },
  inventoryCatalogComponents: (parkId: number, params?: InventoryListParams) =>
    inventoryRequest<InventoryPageEnvelope<InventoryCatalogComponent>>(`/inventory/catalog/components?${new URLSearchParams({ park_id: String(parkId), ...(params?.limit !== undefined ? { limit: String(params.limit) } : {}), ...(params?.offset !== undefined ? { offset: String(params.offset) } : {}) }).toString()}`),
  getInventoryCatalogPart: (parkId: number, partId: number) =>
    inventoryRequest<InventoryCatalogSearchItem>(`/inventory/catalog/parts/${partId}?park_id=${parkId}`),
  createInventoryCatalogComponent: (payload: { park_id: number; name: string }) =>
    inventoryRequest<InventoryCatalogComponent>('/inventory/catalog/components', { method: 'POST', body: inventoryStringify(payload) }),
  updateInventoryCatalogComponent: (id: number, payload: Partial<Pick<InventoryCatalogComponent, 'name' | 'is_active'>>) =>
    inventoryRequest<InventoryCatalogComponent>(`/inventory/catalog/components/${id}`, { method: 'PATCH', body: inventoryStringify(payload) }),
  createInventoryCatalogPart: (payload: { park_id: number; component_id: number; name: string; article: string }) =>
    inventoryRequest<InventoryCatalogPart>('/inventory/catalog/parts', { method: 'POST', body: inventoryStringify(payload) }),
  updateInventoryCatalogPart: (id: number, payload: Partial<Pick<InventoryCatalogPart, 'component_id' | 'name' | 'article' | 'is_active'>>) =>
    inventoryRequest<InventoryCatalogPart>(`/inventory/catalog/parts/${id}`, { method: 'PATCH', body: inventoryStringify(payload) }),
  mergeInventoryCatalogPart: (id: number, targetPartId: number) =>
    inventoryRequest<InventoryCatalogPart>(`/inventory/catalog/parts/${id}/merge`, { method: 'POST', body: inventoryStringify({ target_part_id: targetPartId }) }),
  updateInventoryStock: (parkId: number, partId: number, payload: Pick<InventoryStockView, 'minimum_quantity' | 'location' | 'is_active'>) =>
    inventoryRequest<InventoryStockView>(`/inventory/parks/${parkId}/stocks/${partId}`, { method: 'PUT', body: inventoryStringify(payload) }),
  inventoryReceipts: (parkId: number, params?: InventoryListParams) =>
    inventoryRequest<InventoryPageEnvelope<InventoryReceipt>>(`/inventory/parks/${parkId}/receipts${inventoryListQuery(params)}`),
  createInventoryReceipt: (parkId: number, payload: InventoryReceiptInput) =>
    inventoryRequest<InventoryReceipt>(`/inventory/parks/${parkId}/receipts`, { method: 'POST', body: inventoryStringify(payload) }),
  updateInventoryReceipt: (parkId: number, receiptId: number, payload: Partial<InventoryReceiptInput> & { revision: string }) =>
    inventoryRequest<InventoryReceipt>(`/inventory/parks/${parkId}/receipts/${receiptId}`, { method: 'PATCH', body: inventoryStringify(payload) }),
  postInventoryReceipt: (parkId: number, receiptId: number, revision: string) =>
    inventoryRequest<InventoryReceipt>(`/inventory/parks/${parkId}/receipts/${receiptId}/post`, { method: 'POST', body: inventoryStringify({ revision }) }),
  cancelInventoryReceipt: (parkId: number, receiptId: number, revision: string) =>
    inventoryRequest<InventoryReceipt>(`/inventory/parks/${parkId}/receipts/${receiptId}/cancel`, { method: 'POST', body: inventoryStringify({ revision }) }),
  reverseInventoryReceipt: (parkId: number, receiptId: number, reason: string) =>
    inventoryRequest<InventoryReceipt>(`/inventory/parks/${parkId}/receipts/${receiptId}/reverse`, { method: 'POST', body: inventoryStringify({ reason }) }),
  inventoryCounts: (parkId: number, params?: InventoryListParams) =>
    inventoryRequest<InventoryPageEnvelope<InventoryCount>>(`/inventory/parks/${parkId}/counts${inventoryListQuery(params)}`),
  createInventoryCount: (parkId: number, payload: { name: string; scope: InventoryCountScope }) =>
    inventoryRequest<InventoryCount>(`/inventory/parks/${parkId}/counts`, { method: 'POST', body: inventoryStringify(payload) }),
  updateInventoryCount: (parkId: number, countId: number, lines: InventoryCountLineInput[]) =>
    inventoryRequest<InventoryCount>(`/inventory/parks/${parkId}/counts/${countId}`, { method: 'PATCH', body: inventoryStringify({ lines }) }),
  postInventoryCount: (parkId: number, countId: number) =>
    inventoryRequest<InventoryCount>(`/inventory/parks/${parkId}/counts/${countId}/post`, { method: 'POST' }),
  refreshInventoryCount: (parkId: number, countId: number) =>
    inventoryRequest<InventoryCount>(`/inventory/parks/${parkId}/counts/${countId}/refresh`, { method: 'POST' }),
  cancelInventoryCount: (parkId: number, countId: number) =>
    inventoryRequest<InventoryCount>(`/inventory/parks/${parkId}/counts/${countId}/cancel`, { method: 'POST' }),
  downloadInventoryExport,
  inventoryMovements: (parkId: number) => inventoryRequest<InventoryMovement[]>(`/inventory/movements?park_id=${parkId}`),
  inventoryComponentPhotoUrl: (id: number) => `/api/inventory/components/${id}/photo`,
  inventoryPartPhotoUrl: (id: number) => `/api/inventory/parts/${id}/photo`,
  createInventoryComponent: (parkId: number, name: string, photo?: File | null) => {
    const form = new FormData(); form.append('park_id', String(parkId)); form.append('name', name)
    if (photo) form.append('photo', photo, photo.name)
    return inventoryFormRequest<InventoryComponent>('/inventory/components', form)
  },
  createInventoryPart: (payload: { park_id: number; component_id: number; name: string; article: string; quantity: InventoryInt64; minimum_quantity: InventoryInt64; location: string; photo?: File | null }) => {
    const form = new FormData()
    Object.entries(payload).forEach(([key, value]) => { if (key !== 'photo') form.append(key, String(value)) })
    if (payload.photo) form.append('photo', payload.photo, payload.photo.name)
    return inventoryFormRequest<InventoryPart>('/inventory/parts', form)
  },
  updateInventoryPart: (id: number, payload: Partial<Pick<InventoryPart, 'name' | 'article' | 'component_id' | 'location' | 'minimum_quantity' | 'is_active'>>) => inventoryRequest<InventoryPart>(`/inventory/parts/${id}`, { method: 'PATCH', body: inventoryStringify(payload) }),
  moveInventoryStock: (id: number, kind: 'receipt' | 'writeoff' | 'adjustment', quantity: InventoryInt64, note?: string) => inventoryRequest<InventoryMovement>(`/inventory/parts/${id}/movements`, { method: 'POST', body: inventoryStringify({ kind, quantity, note }) }),
  writeoffInventoryForTask: (issueKey: string, partId: number, quantity: InventoryInt64, idempotencyKey: string) => inventoryRequest<InventoryMovement>(`/inventory/tasks/${encodeURIComponent(issueKey)}/writeoff`, { method: 'POST', body: inventoryStringify({ part_id: partId, quantity, idempotency_key: idempotencyKey }) }),
  reportsMine: () => request<Report[]>('/reports/mine'),
  reportsInbox: (parkId?: number) =>
    request<Report[]>(
      parkId == null
        ? '/reports/inbox'
        : `/reports/inbox?park_id=${parkId}`,
    ),
  report: (id: number) => request<Report>(`/reports/${id}`),
  reportDelete: (id: number) => request<void>(`/reports/${id}`, { method: 'DELETE' }),
  reportAttach: (id: number, kind: ReportAttachmentKind, file: File) => {
    const form = new FormData()
    form.append('kind', kind)
    form.append('file', file, file.name)
    return requestForm<ReportAttachment>(`/reports/${id}/attachments`, form)
  },
  reportAttachmentUrl: (reportId: number, attachmentId: number) =>
    `/api/reports/${reportId}/attachments/${attachmentId}`,
  createReport: (payload: ReportCreatePayload) =>
    request<Report>('/reports', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  reportReturn: (id: number, comment: string) =>
    request<Report>(`/reports/${id}/return`, {
      method: 'POST',
      body: JSON.stringify({ comment }),
    }),
  reportResubmit: (id: number, payload: Pick<ReportCreatePayload, 'title' | 'body' | 'tracker_key' | 'tracker_url'>) =>
    request<Report>(`/reports/${id}/resubmit`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  reportDone: (id: number) =>
    request<Report>(`/reports/${id}/done`, { method: 'POST' }),
  reportEscalate: (id: number, comment: string) =>
    request<Report>(`/reports/${id}/escalate`, {
      method: 'POST',
      body: JSON.stringify({ comment }),
    }),
  reportsBadge: (parkId?: number) =>
    request<ReportBadge>(
      parkId == null
        ? '/reports/badge'
        : `/reports/badge?park_id=${parkId}`,
    ),
  opsSystemHealth: () => request<SystemHealth>('/admin/ops/system-health'),
  opsAvailableUpdate: () => request<AvailableUpdate>('/admin/ops/available-update'),
  opsInspectUpdate: (file: File) => {
    const form = new FormData()
    form.append('archive', file, file.name)
    return requestForm<UpdateInspection>('/admin/ops/update/inspect', form)
  },
  opsApproveUpdate: (inspection_id: string, confirm: 'ОБНОВИТЬ') =>
    request<OpsJob>('/admin/ops/update/approve', { method: 'POST', body: JSON.stringify({ inspection_id, confirm }) }),
  opsApproveGithubUpdate: (release_id: number, confirm: 'ОБНОВИТЬ') =>
    request<OpsJob>('/admin/ops/github-update/approve', { method: 'POST', body: JSON.stringify({ release_id, confirm }) }),
  opsDiagnostics: () => request<OpsJob>('/admin/ops/diagnostics', { method: 'POST' }),
  opsRepair: () => request<OpsJob>('/admin/ops/repair', { method: 'POST' }),
  opsDiagnosticArtifact: () => requestBlob('/admin/ops/diagnostic-artifact'),
  opsMaintenance: () => request<OpsMaintenance>('/ops/maintenance'),
  opsJob: () => request<OpsJob>('/admin/ops/job'),
  opsAbort: () => request<OpsJob>('/admin/ops/abort', { method: 'POST' }),
  opsSnapshot: () => request<OpsJob>('/admin/ops/snapshot', { method: 'POST' }),
  opsArtifact: () => requestBlob('/admin/ops/artifact'),
  opsRestore: (file: File, confirm: string) => {
    const form = new FormData()
    form.append('confirm', confirm)
    form.append('archive', file, file.name)
    return requestForm<OpsJob>('/admin/ops/restore', form)
  },
  opsUpdate: (file: File, confirm: string) => {
    const form = new FormData()
    form.append('confirm', confirm)
    form.append('archive', file, file.name)
    return requestForm<OpsJob>('/admin/ops/update', form)
  },
}
