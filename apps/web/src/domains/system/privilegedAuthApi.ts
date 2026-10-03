import { ApiError, fetchWithTimeout } from '../../api'

export type PrivilegedAuthClient = {
  status(): Promise<{ enrolled: boolean }>
  beginEnrollment(): Promise<{ secret: string }>
  confirmEnrollment(value: { password: string, code: string }): Promise<{ recovery_codes: string[] }>
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  return fetchWithTimeout(`/api/admin/privileged-auth${path}`, {
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
  })
}

export const privilegedAuthClient: PrivilegedAuthClient = {
  status: () => json('/status'),
  beginEnrollment: () => json('/enrollment', { method: 'POST' }),
  confirmEnrollment: value => json('/enrollment/confirm', {
    method: 'POST', body: JSON.stringify(value),
  }),
}
