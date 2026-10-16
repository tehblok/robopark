export type DesktopAgentStatus = {
  version: 1
  account: {
    state: 'signed_out' | 'checking' | 'signed_in' | 'error'
    label: string
    role: string | null
    registered: boolean
  }
  service: { state: 'running' | 'starting' | 'stopped' | 'error' }
  server: { state: 'connected' | 'connecting' | 'offline' | 'error'; lastSuccessAt: string | null }
  sync: null | {
    state: 'syncing' | 'pending' | 'offline' | 'error'
    pending: number
    conflicts: number
    lastSuccessAt: string | null
    message: string | null
  }
  corporate: {
    state: 'ready' | 'login_required' | 'checking' | 'denied' | 'unavailable'
    canLogin: boolean
    ownerLoginConfigured: boolean
    probeConfigured: boolean
    tracker: CorporateAccessState
    inventory: CorporateAccessState
    emergency: CorporateAccessState
    message: string | null
  }
  autostart: { supported: boolean; enabled: boolean }
  work: { paused: boolean }
  legacyWorkspaceAvailable: boolean | null
  update: {
    state: 'current' | 'checking' | 'available' | 'error'
    currentVersion: string
    availableVersion: string | null
    message: string | null
  }
}

export type CorporateAccessState = 'ready' | 'login_required' | 'checking' | 'denied' | 'unavailable' | 'unverified'

export type DesktopAgentAction =
  | { action: 'connect' | 'corporateLogin' | 'checkUpdate' | 'reloadKeychain' | 'openLegacyWorkspace' }
  | { action: 'autostart' | 'pause'; enabled: boolean }

export type DeviceClient = {
  status: () => Promise<DesktopAgentStatus>
  action: (action: DesktopAgentAction) => Promise<DesktopAgentStatus>
}

export type DiagnosticService = 'server' | 'emergency' | 'inventory' | 'tracker'
export type DiagnosticRoute = 'direct' | 'server'
export type DiagnosticRequest = { service: DiagnosticService; route: DiagnosticRoute; robot: string }
export type DiagnosticEvent = {
  id: string | number; time: string; service: string; stage: string; status: string; code?: string; elapsedMs?: number
}
export type DiagnosticResult = {
  service: string; route: string; robot?: string; status: string; httpStatus?: number; code?: string; elapsedMs?: number
}
export type DiagnosticSnapshot = { events: DiagnosticEvent[]; running: boolean; results: DiagnosticResult[] }
export type DiagnosticClient = {
  snapshot: () => Promise<DiagnosticSnapshot>
  run: (request: DiagnosticRequest) => Promise<DiagnosticSnapshot>
}

export class DiagnosticsUnavailableError extends Error {
  constructor() { super('desktop_diagnostics_unavailable'); this.name = 'DiagnosticsUnavailableError' }
}

async function readDiagnostics(response: Response): Promise<DiagnosticSnapshot> {
  if (response.status === 404) throw new DiagnosticsUnavailableError()
  if (!response.ok) throw new Error(`desktop_diagnostics_http_${response.status}`)
  const value = await response.json() as Partial<DiagnosticSnapshot>
  if (typeof value.running !== 'boolean' || !Array.isArray(value.events) || !Array.isArray(value.results)) {
    throw new Error('desktop_diagnostics_invalid')
  }
  return value as DiagnosticSnapshot
}

export const diagnosticClient: DiagnosticClient = {
  snapshot() {
    return fetchWithTimeout('/__robopark/agent/diagnostics', {
      credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
    }, 10_000, readDiagnostics, { authFailureScope: 'mutation' })
  },
  run(request) {
    return fetchWithTimeout('/__robopark/agent/diagnostics', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    }, 10_000, readDiagnostics, { authFailureScope: 'mutation' })
  },
}

async function readStatus(response: Response): Promise<DesktopAgentStatus> {
  if (!response.ok) throw new Error(`desktop_agent_http_${response.status}`)
  const value = await response.json() as Partial<DesktopAgentStatus>
  if (value.version !== 1 || !value.account || !value.service || !value.server || value.sync === undefined || !value.corporate
    || !value.autostart || !value.work || value.legacyWorkspaceAvailable === undefined || !value.update) throw new Error('desktop_agent_status_invalid')
  return value as DesktopAgentStatus
}

export const deviceClient: DeviceClient = {
  async status() {
    return fetchWithTimeout('/__robopark/agent/status', {
      credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' },
    }, 10_000, readStatus)
  },
  async action(action) {
    return fetchWithTimeout('/__robopark/agent/action', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(action),
    }, 10_000, readStatus)
  },
}
import { fetchWithTimeout } from '../../api'
