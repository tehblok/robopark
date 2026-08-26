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
}

export type User = {
  id: number
  username: string
  role: string
  access_status: string
  tracker_login?: string | null
  must_change_password?: boolean
  parks: Park[]
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
  error_banner: string | null
  lat: number | null
  lon: number | null
  heading_deg: number | null
  wheels_fault: string[]
}

export type EmergencyViewerRole = 'mechanic' | 'operator' | 'admin' | 'royal'

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

export type TrackerIssueDetail = TrackerIssue & {
  resolution?: string | null
  description?: string | null
  reporter?: TrackerPerson | null
  components?: string[]
  attachments?: TrackerAttachment[]
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
}
export type TrackerTransition = { id: string; display: string }
export type TrackerActionResult = { key: string; action: string; status: string; actor: string; performed_at: string }

export type DashboardMovingItem = {
  key: string
  summary: string
}

export type DashboardSummary = {
  park_id: number
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

export class ApiError extends Error {
  status: number
  detail: string | null

  constructor(status: number, detail: string | null = null) {
    super(detail ?? String(status))
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
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
  const response = await fetch(`/api${path}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  })

  if (!response.ok) {
    const detail = await readErrorDetail(response)
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

async function requestBlob(path: string): Promise<Blob> {
  const response = await fetch(`/api${path}`, { credentials: 'include' })
  if (!response.ok) {
    const detail = await readErrorDetail(response)
    throw new ApiError(response.status, detail)
  }
  return response.blob()
}

export const api = {
  me: () => request<User>('/auth/me'),
  login: (username: string, password: string) =>
    request<void>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  changePassword: (current_password: string, new_password: string) =>
    request<void>('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ current_password, new_password }),
    }),
  register: (shared_password: string, username: string, password: string) =>
    request<User>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ shared_password, username, password }),
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
  mechanics: () => request<Mechanic[]>('/admin/mechanics'),
  createMechanic: (username: string, password: string, park_id: number) =>
    request<Mechanic>('/admin/mechanics', {
      method: 'POST',
      body: JSON.stringify({ username, password, park_id }),
    }),
  updateMechanic: (
    mechanicId: number,
    changes: {
      password?: string
      park_id?: number
      is_active?: boolean
      tracker_login?: string | null
      must_change_password?: boolean
    },
  ) =>
    request<Mechanic>(`/admin/mechanics/${mechanicId}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    }),
  operatorParks: () => request<Park[]>('/operator/parks'),
  availableParks: () => request<Park[]>('/operator/available-parks'),
  operatorParkRequests: () => request<ParkRequest[]>('/operator/park-requests'),
  requestPark: (parkId: number) =>
    request<ParkRequest>('/operator/park-requests', {
      method: 'POST',
      body: JSON.stringify({ park_id: parkId }),
    }),
  accessRequests: () => request<AccessRequest[]>('/admin/access-requests'),
  approveAccessRequest: (userId: number, parkIds: number[]) =>
    request<void>(`/admin/access-requests/${userId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ park_ids: parkIds }),
    }),
  rejectAccessRequest: (userId: number) =>
    request<void>(`/admin/access-requests/${userId}/reject`, { method: 'POST' }),
  adminParkRequests: () =>
    request<ParkRequest[]>('/admin/park-requests'),
  resolveParkRequest: (requestId: number, resolution: 'approve' | 'reject') =>
    request<void>(`/admin/park-requests/${requestId}/${resolution}`, {
      method: 'POST',
    }),
  mechanicTasks: (status = 'all') =>
    request<MechanicTasks>(`/mechanic/tasks?status=${encodeURIComponent(status)}`),
  mechanicRobotTickets: (query: string) =>
    request<{ query: string; items: Blocker[] }>(
      `/mechanic/robots/${encodeURIComponent(query)}/tickets`,
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
    limit?: number
    offset?: number
  }) => {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
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
  trackerIssue: (key: string) => request<TrackerIssueDetail>(`/tracker/issues/${encodeURIComponent(key)}`),
  trackerComments: (key: string) =>
    request<TrackerComment[]>(`/tracker/issues/${encodeURIComponent(key)}/comments`),
  trackerTransitions: (key: string) =>
    request<TrackerTransition[]>(`/tracker/transitions/${encodeURIComponent(key)}`),
  trackerComment: (key: string, text: string) =>
    request<TrackerActionResult>(`/tracker/issues/${encodeURIComponent(key)}/comment`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    }),
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
}
