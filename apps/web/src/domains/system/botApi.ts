import { ApiError, fetchWithTimeout } from '../../api'

export type BotStatus = {
  desired_enabled: boolean
  runtime_state: string
  token_configured: boolean
  token_masked: string | null
  token_updated_at: string | null
  token_encrypted: boolean
}

export type BotImportReport = {
  total_bytes: number
  files: { filename: string; byte_count: number; sha256: string }[]
}

export type BotClient = {
  getStatus(): Promise<BotStatus>
  setToken(token: string): Promise<BotStatus>
  setEnabled(enabled: boolean): Promise<BotStatus>
  previewImport(archive: File): Promise<BotImportReport>
  executeImport(archive: File): Promise<BotImportReport>
}

async function request<T>(path: string, init: RequestInit = {}, timeout = 15_000): Promise<T> {
  return fetchWithTimeout(`/api/admin/bot${path}`, { credentials: 'include', ...init }, timeout, async response => {
    if (!response.ok) {
      let detail: unknown = null
      try { detail = (await response.json()).detail ?? null } catch { /* bounded error */ }
      throw new ApiError(response.status, detail)
    }
    return await response.json() as T
  })
}

function archiveBody(archive: File): FormData {
  const body = new FormData()
  body.append('archive', archive)
  return body
}

export const botClient: BotClient = {
  getStatus: () => request(''),
  setToken: token => request('/token', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }),
  }),
  setEnabled: enabled => request('/enabled', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
  }),
  previewImport: archive => request('/import/preview', { method: 'POST', body: archiveBody(archive) }, 60_000),
  executeImport: archive => request('/import/execute', { method: 'POST', body: archiveBody(archive) }, 60_000),
}
