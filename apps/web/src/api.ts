export type Park = {
  id: number
  name: string
  tag: string
  is_active?: boolean
  tracker_queue?: string | null
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
  parks: Park[]
}

export type IntegrationSettings = {
  tracker_token_masked: string | null
  tracker_token_updated_at: string | null
  emergency_cookie_masked: string | null
  emergency_cookie_updated_at: string | null
  emergency_cookie_valid: boolean | null
}

export type Mechanic = {
  id: number
  username: string
  is_active: boolean
  created_at: string
  park: Park
}

export type Blocker = {
  key: string
  summary: string
  status: string
  robot: string | null
  created_at: string | null
  hours_created: string | null
  url: string
  bucket: string
}

export type MechanicTasks = {
  park_tag: string
  status: string
  counts: Record<string, number>
  items: Blocker[]
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
    throw new Error(String(response.status))
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

export const api = {
  me: () => request<User>('/auth/me'),
  login: (username: string, password: string) =>
    request<void>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
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
  mechanics: () => request<Mechanic[]>('/admin/mechanics'),
  createMechanic: (username: string, password: string, park_id: number) =>
    request<Mechanic>('/admin/mechanics', {
      method: 'POST',
      body: JSON.stringify({ username, password, park_id }),
    }),
  updateMechanic: (
    mechanicId: number,
    changes: { password?: string; park_id?: number; is_active?: boolean },
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
}
