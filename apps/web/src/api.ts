export type Park = {
  id: number
  name: string
  tag: string
  is_active?: boolean
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
  createPark: (name: string, tag: string) =>
    request<Park>('/parks', {
      method: 'POST',
      body: JSON.stringify({ name, tag }),
    }),
  updatePark: (parkId: number, changes: Partial<Pick<Park, 'name' | 'tag' | 'is_active'>>) =>
    request<Park>(`/parks/${parkId}`, {
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
}
