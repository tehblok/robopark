import { DesktopSessionRequiredError, initializeDesktopTransport } from '../../pwa/desktopTransport'
import { hydrateDesktopOfflineIdentity } from '../../shared/auth/offlineIdentity'

const STARTUP_TIMEOUT_MS = 5_000

export class StartupTimeoutError extends Error {
  constructor() {
    super('startup_timeout')
    this.name = 'StartupTimeoutError'
  }
}

export async function initializeApplicationStartup(
  initializeTransport: () => Promise<void> = initializeDesktopTransport,
  hydrateIdentity: () => Promise<void> = () => hydrateDesktopOfflineIdentity({ propagateFailure: true }),
  timeoutMs = STARTUP_TIMEOUT_MS,
): Promise<void> {
  let timeout: ReturnType<typeof setTimeout> | undefined
  const deadline = new Promise<never>((_, reject) => {
    timeout = setTimeout(() => reject(new StartupTimeoutError()), timeoutMs)
  })
  try {
    await Promise.race([(async () => {
      await initializeTransport()
      try { await hydrateIdentity() }
      catch (error) {
        // A direct localhost tab has no per-tab native capability. It may show
        // the public login flow, but must not decrypt or delete saved identity.
        if (error instanceof DesktopSessionRequiredError) return
        throw error
      }
    })(), deadline])
  } finally {
    if (timeout !== undefined) clearTimeout(timeout)
  }
}
