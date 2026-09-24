import type { SyncState } from './syncEngine'

type WaitingWorker = { postMessage: (message: unknown) => void }
type WorkerRegistration = { update: () => Promise<unknown>, waiting?: WaitingWorker | null }

type WorkerEnvironment = {
  production: boolean
  secure: boolean
  serviceWorker?: {
    register: (script: string, options: { scope: string }) => Promise<WorkerRegistration>
    addEventListener?: (name: 'message' | 'controllerchange', callback: (event: { data?: unknown, ports?: { postMessage: (value: unknown) => void }[] }) => void) => void
  }
  onFocus?: (callback: () => void) => void
  onVisible?: (callback: () => void) => void
  reload?: () => void
}

let waitingRegistration: WorkerRegistration | null = null
let activationRequested = false
let syncState: Pick<SyncState, 'status' | 'pending' | 'conflicts'> | null = { status: 'idle', pending: 0, conflicts: 0 }
const updateListeners = new Set<() => void>()

export function setServiceWorkerSyncState(state: Pick<SyncState, 'status' | 'pending' | 'conflicts'> | null): void {
  syncState = state
}

function safeToActivate(state: typeof syncState): boolean {
  return state?.status === 'idle' && state.pending === 0 && state.conflicts === 0
}

export function serviceWorkerUpdateReady(): boolean {
  return Boolean(waitingRegistration?.waiting)
}

export function subscribeServiceWorkerUpdate(listener: () => void): () => void {
  updateListeners.add(listener)
  return () => updateListeners.delete(listener)
}

function rememberWaitingWorker(registration: WorkerRegistration) {
  waitingRegistration = registration
  updateListeners.forEach(listener => listener())
}

export function activateServiceWorkerWhenSafe(
  registration: { waiting?: WaitingWorker | null } = waitingRegistration ?? {},
  state: Pick<SyncState, 'status' | 'pending' | 'conflicts'>,
): boolean {
  if (!registration.waiting || !safeToActivate(state)) return false
  registration.waiting.postMessage({ type: 'ACTIVATE_WHEN_SAFE', state })
  activationRequested = true
  return true
}

export async function registerServiceWorker(environment: WorkerEnvironment): Promise<boolean> {
  if (!environment.production || !environment.secure || !environment.serviceWorker) return false
  try {
    const registration = await environment.serviceWorker.register('/sw.js', { scope: '/' })
    if (registration.waiting) rememberWaitingWorker(registration)
    environment.serviceWorker.addEventListener?.('message', (event) => {
      const type = (event.data as { type?: string } | undefined)?.type
      if (type === 'UPDATE_READY') rememberWaitingWorker(registration)
      if (type === 'CHECK_ACTIVATION_SAFETY') event.ports?.[0]?.postMessage({ safe: safeToActivate(syncState) })
    })
    environment.serviceWorker.addEventListener?.('controllerchange', () => {
      if (!activationRequested) return
      activationRequested = false
      waitingRegistration = null
      updateListeners.forEach(listener => listener())
      environment.reload?.()
    })
    let lastUpdateCheck = 0
    const checkForUpdate = () => {
      const now = Date.now()
      if (now - lastUpdateCheck < 5 * 60_000) return
      lastUpdateCheck = now
      void registration.update().catch(() => {})
    }
    environment.onFocus?.(checkForUpdate)
    environment.onVisible?.(checkForUpdate)
    return true
  } catch {
    // PWA support is optional; a denied registration must not affect the site.
    return false
  }
}
