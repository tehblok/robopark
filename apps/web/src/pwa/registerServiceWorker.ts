import type { SyncState } from './syncEngine'

type WaitingWorker = { postMessage: (message: unknown) => void }
type WorkerRegistration = { update: () => Promise<unknown>, waiting?: WaitingWorker | null }

type WorkerEnvironment = {
  production: boolean
  secure: boolean
  serviceWorker?: {
    register: (script: string, options: { scope: string }) => Promise<WorkerRegistration>
    addEventListener?: (name: 'message', callback: (event: { data?: unknown }) => void) => void
  }
  onFocus?: (callback: () => void) => void
  onVisible?: (callback: () => void) => void
}

let waitingRegistration: WorkerRegistration | null = null
const updateListeners = new Set<() => void>()

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
  state: Pick<SyncState, 'status' | 'pending'>,
): boolean {
  if (!registration.waiting || state.status === 'syncing' || state.pending > 0) return false
  registration.waiting.postMessage({ type: 'ACTIVATE_WHEN_SAFE' })
  if (registration === waitingRegistration) {
    waitingRegistration = null
    updateListeners.forEach(listener => listener())
  }
  return true
}

export async function registerServiceWorker(environment: WorkerEnvironment): Promise<boolean> {
  if (!environment.production || !environment.secure || !environment.serviceWorker) return false
  try {
    const registration = await environment.serviceWorker.register('/sw.js', { scope: '/' })
    if (registration.waiting) rememberWaitingWorker(registration)
    environment.serviceWorker.addEventListener?.('message', (event) => {
      if ((event.data as { type?: string } | undefined)?.type === 'UPDATE_READY') rememberWaitingWorker(registration)
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
