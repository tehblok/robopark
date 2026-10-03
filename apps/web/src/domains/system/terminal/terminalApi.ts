import { ApiError, fetchWithTimeout } from '../../../api'

export type TerminalProfile = 'maintenance' | 'root'
export type TerminalSessionState = 'starting' | 'active' | 'detached' | 'ended'

export type TerminalCapabilities = {
  available: boolean
  reason?: string
  boot_id?: string
  broker_epoch?: string
  capability_revision?: string
  profiles?: TerminalProfile[]
  active_sessions?: number
}

export type SessionOut = {
  id: string
  profile: TerminalProfile
  state: TerminalSessionState
  expires_at: string
  broker_epoch: string
  termination_reason: string | null
}

export type TerminalApiClient = {
  capabilities(): Promise<TerminalCapabilities>
  reauthorize(value: { password: string; code: string; operation_kind: `terminal.open.${TerminalProfile}`; operation_id: string; capability_revision: string }): Promise<{ token: string; expires_in: number }>
  create(value: { id: string; profile: TerminalProfile; capability_revision: string }, authorization: string): Promise<SessionOut>
  list(): Promise<SessionOut[]>
  attachTicket(id: string): Promise<{ ticket: string; expires_in: number }>
  terminate(id: string): Promise<SessionOut>
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  return fetchWithTimeout(`/api${path}`, {
    credentials: 'include', cache: 'no-store', ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  }, 15_000, async response => {
    if (!response.ok) {
      let detail: unknown = null
      try { detail = (await response.json()).detail ?? null } catch { /* bounded error */ }
      throw new ApiError(response.status, detail)
    }
    return response.status === 204 ? undefined as T : await response.json() as T
  }, { authFailureScope: 'query', sessionPreserving401Details: ['invalid_password', 'invalid_totp', 'invalid_credentials'] })
}

export const terminalApi: TerminalApiClient = {
  capabilities: () => json('/admin/terminal/capabilities'),
  reauthorize: value => json('/admin/privileged-auth/reauthorize', { method: 'POST', body: JSON.stringify(value) }),
  create: (value, authorization) => json('/admin/terminal/sessions', {
    method: 'POST', headers: { 'X-Privileged-Authorization': authorization }, body: JSON.stringify(value),
  }),
  list: () => json('/admin/terminal/sessions'),
  attachTicket: id => json(`/admin/terminal/sessions/${encodeURIComponent(id)}/attach-ticket`, { method: 'POST' }),
  terminate: id => json(`/admin/terminal/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
