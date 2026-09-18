type WorkerRegistration = { update: () => Promise<unknown> }

type WorkerEnvironment = {
  production: boolean
  secure: boolean
  serviceWorker?: { register: (script: string, options: { scope: string }) => Promise<WorkerRegistration> }
  onFocus?: (callback: () => void) => void
  onVisible?: (callback: () => void) => void
}

export async function registerServiceWorker(environment: WorkerEnvironment): Promise<boolean> {
  if (!environment.production || !environment.secure || !environment.serviceWorker) return false
  try {
    const registration = await environment.serviceWorker.register('/sw.js', { scope: '/' })
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
