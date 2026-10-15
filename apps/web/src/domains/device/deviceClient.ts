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
