export type Park = {
  id: number
  name: string
  tag: string
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
}
