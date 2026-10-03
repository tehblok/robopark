import type { SyncState } from './syncEngine'

type WaitingWorker = { postMessage: (message: unknown) => void }
type WorkerRegistration = { update: () => Promise<unknown>, waiting?: WaitingWorker | null }
type ActivationPort = { postMessage: (value: unknown) => void, onmessage?: ((event: { data?: unknown }) => void) | null }
type StatefulWorker = {
  state: string
  addEventListener: (name: 'statechange', callback: () => void, options: { once: boolean }) => void
  removeEventListener?: (name: 'statechange', callback: () => void) => void
}

type WorkerEnvironment = {
  production: boolean
  secure: boolean
  serviceWorker?: {
    controller?: StatefulWorker | null
    register: (script: string, options: { scope: string }) => Promise<WorkerRegistration>
    addEventListener?: (name: 'message' | 'controllerchange', callback: (event: { data?: unknown, source?: StatefulWorker | null, ports?: ActivationPort[] }) => void) => void
  }
  onFocus?: (callback: () => void) => void
  onVisible?: (callback: () => void) => void
  reload?: () => void
}

let waitingRegistration: WorkerRegistration | null = null
let activeRegistration: WorkerRegistration | null = null
let activationRequested = false
let authState: { loading: boolean, accountId: number | null } | null = null
let syncState: { accountId: number, value: Pick<SyncState, 'status' | 'pending' | 'conflicts'> } | null = null
let localWorkInFlight = 0
let activationFence: ActivationPort | null = null
const updateListeners = new Set<() => void>()

export function setServiceWorkerAuthState(state: { loading: boolean, accountId: number | null } | null): void {
  authState = state
  vetoUnsafeActivation()
  requestWaitingActivationIfSafe()
}

export function setServiceWorkerSyncState(state: Pick<SyncState, 'status' | 'pending' | 'conflicts'> | null, accountId?: number): void {
  syncState = state && accountId !== undefined ? { accountId, value: state } : null
  vetoUnsafeActivation()
  requestWaitingActivationIfSafe()
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
      requestWaitingActivationIfSafe()
    })
  } catch (error) {
    localWorkInFlight -= 1
    requestWaitingActivationIfSafe()
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
  requestWaitingActivationIfSafe()
}

function requestWaitingActivationIfSafe(): boolean {
  const worker = waitingRegistration?.waiting
  if (!worker || !safeToActivate()) return false
  const state = authState?.accountId === null
    ? { status: 'idle' as const, pending: 0, conflicts: 0 }
    : syncState?.value
  if (!state) return false
  worker.postMessage({ type: 'ACTIVATE_WHEN_SAFE', state })
  activationRequested = true
  return true
}

export function activateServiceWorkerWhenSafe(
  registration: { waiting?: WaitingWorker | null } = waitingRegistration ?? {},
  state: Pick<SyncState, 'status' | 'pending' | 'conflicts'>,
): boolean {
  if (!registration.waiting || !safeState(state) || !safeToActivate()) return false
  registration.waiting.postMessage({ type: 'ACTIVATE_WHEN_SAFE', state })
  activationRequested = true
  return true
}

export async function requestServiceWorkerUpdate(): Promise<void> {
  if (!activeRegistration) return
  await activeRegistration.update()
}

export async function registerServiceWorker(environment: WorkerEnvironment): Promise<boolean> {
  if (!environment.production || !environment.secure || !environment.serviceWorker) return false
  try {
    const registration = await environment.serviceWorker.register('/sw.js', { scope: '/' })
    let stopActivationRecovery: (() => void) | undefined
    const recoverFailedActivation = (port: ActivationPort, worker?: StatefulWorker | null) => {
      stopActivationRecovery?.()
      const abandon = (expired: boolean) => {
        if (activationFence !== port) return
        if (expired) {
          try { port.postMessage({ type: 'VETO_ACTIVATION' }) } catch { /* The worker may have terminated. */ }
        }
        activationFence = null
        activationRequested = false
        stopActivationRecovery?.()
        if (worker?.state === 'redundant') waitingRegistration = null
        updateListeners.forEach(listener => listener())
      }
      const onStateChange = () => { if (worker?.state === 'redundant') abandon(false) }
      // A terminated worker may never release its port or take control. Bound
      // this fence; an abandoned attempt must not later reload new local work.
      const timeout = setTimeout(() => abandon(true), 60_000)
      worker?.addEventListener('statechange', onStateChange, { once: false })
      stopActivationRecovery = () => {
        clearTimeout(timeout)
        worker?.removeEventListener?.('statechange', onStateChange)
        stopActivationRecovery = undefined
      }
      onStateChange()
    }
    activeRegistration = registration
    if (registration.waiting) rememberWaitingWorker(registration)
    environment.serviceWorker.addEventListener?.('message', (event) => {
      const type = (event.data as { type?: string } | undefined)?.type
      if (type === 'UPDATE_READY') rememberWaitingWorker(registration)
      if (type === 'PREPARE_ACTIVATION') {
        const port = event.ports?.[0]
        if (!port) return
        const safe = !activationFence && safeToActivate()
        if (safe) {
          activationRequested = true
          activationFence = port
          recoverFailedActivation(port, event.source)
        }
        port.onmessage = (reply) => {
          if ((reply.data as { type?: string } | undefined)?.type !== 'RELEASE_ACTIVATION') return
          if (activationFence === port) {
            activationFence = null
            activationRequested = false
            stopActivationRecovery?.()
          }
          port.postMessage({ type: 'RELEASED_ACTIVATION' })
        }
        port.postMessage({ safe, reloadOnControllerChange: true })
      }
    })
    environment.serviceWorker.addEventListener?.('controllerchange', () => {
      if (!activationRequested) { activationFence = null; stopActivationRecovery?.(); return }
      const controller = environment.serviceWorker?.controller
      const reloadActivatedDocument = () => {
        if (controller?.state === 'redundant' && environment.serviceWorker?.controller === controller) {
          activationFence = null
          activationRequested = false
          stopActivationRecovery?.()
          return
        }
        if (!activationRequested || (controller && (
          environment.serviceWorker?.controller !== controller || controller.state !== 'activated'
        ))) return
        activationFence = null
        activationRequested = false
        stopActivationRecovery?.()
        waitingRegistration = null
        updateListeners.forEach(listener => listener())
        environment.reload?.()
      }
      // claim() can fire controllerchange while activate.waitUntil is pending.
      // A navigation then waits for activation, while WebKit's client lookup
      // can wait for that navigating document. Finish activation first.
      if (controller && controller.state !== 'activated') {
        controller.addEventListener('statechange', reloadActivatedDocument, { once: true })
      } else reloadActivatedDocument()
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
