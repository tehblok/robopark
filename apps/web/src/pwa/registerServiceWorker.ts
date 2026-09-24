import type { SyncState } from './syncEngine'

type WaitingWorker = { postMessage: (message: unknown) => void }
type WorkerRegistration = { update: () => Promise<unknown>, waiting?: WaitingWorker | null }
type ActivationPort = { postMessage: (value: unknown) => void, onmessage?: ((event: { data?: unknown }) => void) | null }

type WorkerEnvironment = {
  production: boolean
  secure: boolean
  serviceWorker?: {
    register: (script: string, options: { scope: string }) => Promise<WorkerRegistration>
    addEventListener?: (name: 'message' | 'controllerchange', callback: (event: { data?: unknown, ports?: ActivationPort[] }) => void) => void
  }
  onFocus?: (callback: () => void) => void
  onVisible?: (callback: () => void) => void
  reload?: () => void
}

let waitingRegistration: WorkerRegistration | null = null
let activationRequested = false
let authState: { loading: boolean, accountId: number | null } | null = null
let syncState: { accountId: number, value: Pick<SyncState, 'status' | 'pending' | 'conflicts'> } | null = null
let localWorkInFlight = 0
let activationFence: ActivationPort | null = null
const updateListeners = new Set<() => void>()

export function setServiceWorkerAuthState(state: { loading: boolean, accountId: number | null } | null): void {
  authState = state
  vetoUnsafeActivation()
}

export function setServiceWorkerSyncState(state: Pick<SyncState, 'status' | 'pending' | 'conflicts'> | null, accountId?: number): void {
  syncState = state && accountId !== undefined ? { accountId, value: state } : null
  vetoUnsafeActivation()
}

function safeState(state: Pick<SyncState, 'status' | 'pending' | 'conflicts'> | null): boolean {
  return state?.status === 'idle' && state.pending === 0 && state.conflicts === 0
}

function safeToActivate(): boolean {
  if (!authState || authState.loading || localWorkInFlight > 0) return false
  if (authState.accountId === null) return true
  return syncState?.accountId === authState.accountId && safeState(syncState.value)
}

function vetoUnsafeActivation(): void {
  if (activationFence && !safeToActivate()) activationFence.postMessage({ type: 'VETO_ACTIVATION' })
}

export function runLocalWork<T>(work: () => Promise<T>): Promise<T> {
  if (activationFence) return Promise.reject(new Error('pwa_update_in_progress'))
  localWorkInFlight += 1
  try {
    return Promise.resolve(work()).finally(() => {
      localWorkInFlight -= 1
      vetoUnsafeActivation()
    })
  } catch (error) {
    localWorkInFlight -= 1
    return Promise.reject(error)
  }
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
  if (!registration.waiting || !safeState(state)) return false
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
      if (type === 'PREPARE_ACTIVATION') {
        const port = event.ports?.[0]
        if (!port) return
        const safe = !activationFence && safeToActivate()
        if (safe) {
          activationFence = port
          port.onmessage = (reply) => {
            if ((reply.data as { type?: string } | undefined)?.type === 'RELEASE_ACTIVATION' && activationFence === port) activationFence = null
          }
        }
        port.postMessage({ safe })
      }
    })
    environment.serviceWorker.addEventListener?.('controllerchange', () => {
      activationFence = null
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
