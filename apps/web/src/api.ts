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
}

export type IntegrationSettings = {
  tracker_token_masked: string | null
  tracker_token_updated_at: string | null
  tracker_token_encrypted?: boolean
  emergency_cookie_masked: string | null
  emergency_cookie_updated_at: string | null
  emergency_cookie_encrypted?: boolean
  emergency_cookie_valid: boolean | null
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
  online: boolean | null
  speed: number | null
  charge_percent: number | null
  battery1_percent: number | null
  battery2_percent: number | null
  disk_percent: number | null
  mode: string | null
  icp_label: string | null
  icp_ok: boolean | null
  lte_label: string | null
  lte_ok: boolean | null
  connection: 'lte' | 'wire' | null
  error_banner: string | null
  lat: number | null
  lon: number | null
  heading_deg: number | null
  wheels_fault: string[]
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

export type TrackerIssueDetail = TrackerIssue & {
  resolution?: string | null
  description?: string | null
  reporter?: TrackerPerson | null
  components?: string[]
  attachments?: TrackerAttachment[]
  capabilities: TrackerIssueCapabilities
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

export type Report = {
  id: number
  kind: string
  status: string
  park_id: number
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
}

export type OpsMaintenance = {
  active: boolean
  kind: string | null
  operator: boolean
}

export class ApiError extends Error {
  status: number
  detail: string | null
  requestId?: string

  constructor(status: number, detail: string | null = null, requestId?: string) {
    super(detail ?? String(status))
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.requestId = requestId
  }
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

function responseRequestId(response: Response): string | undefined {
  return response.headers.get('X-Request-ID')?.trim() || undefined
}

async function readErrorDetail(response: Response): Promise<string | null> {
  try {
    const body = await response.json() as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return body.detail.map(String).join('; ')
  } catch {
    return null
  }
  return null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
        throw new ApiError(response.status, detail, responseRequestId(response))
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
        throw new ApiError(response.status, detail, responseRequestId(response))
      }
      return response.blob()
    },
  )
}

async function requestForm<T>(path: string, formData: FormData): Promise<T> {
  return fetchWithTimeout(
    `/api${path}`,
    {
      credentials: 'include',
      method: 'POST',
      body: formData,
    },
    FORM_TIMEOUT_MS,
    async (response) => {
      if (!response.ok) {
        const detail = await readErrorDetail(response)
        throw new ApiError(response.status, detail, responseRequestId(response))
      }

      return response.json() as Promise<T>
    },
  )
}

export const api = {
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
    assignee?: string
    untagged?: boolean
    age_hours?: number
    sort?: 'oldest' | 'newest'
    limit?: number
    offset?: number
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
  trackerIssue: (key: string, signal?: AbortSignal) =>
    request<TrackerIssueDetail>(
      `/tracker/issues/${encodeURIComponent(key)}`,
      signal ? { signal } : undefined,
    ),
  trackerComments: (key: string) =>
    request<TrackerComment[]>(`/tracker/issues/${encodeURIComponent(key)}/comments`),
  trackerTransitions: (key: string) =>
    request<TrackerTransition[]>(`/tracker/transitions/${encodeURIComponent(key)}`),
  trackerComment: (key: string, text: string) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/comment`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
  trackerAttach: (key: string, file: File) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return requestForm<TrackerActionResult>(
      `/tracker/issues/${encodeURIComponent(key)}/attachments`,
      form,
    )
  },
  trackerAssign: (key: string, assignee: string) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/assign`, {
      method: 'POST',
      body: JSON.stringify({ assignee }),
    }),
  trackerUnassign: (key: string) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/unassign`, {
      method: 'POST',
    }),
  trackerTransition: (key: string, transition: string, resolution?: string) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/transition`, {
      method: 'POST',
      body: JSON.stringify({ transition, resolution }),
    }),
  trackerClose: (key: string) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/close`, {
      method: 'POST',
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
  dashboardHistory: (parkId: number, days = 7) =>
    request<DashboardHistory>(
      `/dashboard/history?park_id=${parkId}&days=${days}`,
    ),
  reportsMine: () => request<Report[]>('/reports/mine'),
  reportsInbox: (parkId?: number) =>
    request<Report[]>(
      parkId == null
        ? '/reports/inbox'
        : `/reports/inbox?park_id=${parkId}`,
    ),
  report: (id: number) => request<Report>(`/reports/${id}`),
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
