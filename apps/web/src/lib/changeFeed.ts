/** Small, visible-tab-only revision checks. Response bodies stay in the resource hooks. */
const lastSeenRevisions = new Map<string, number>()

export function resetChangeFeedVersionsForTests(): void {
  lastSeenRevisions.clear()
}

export function startChangeFeed({
  identity = '', scope, load, onChange, onAuthorizationFailure,
}: {
  identity?: string
  scope: string
  load: (scope: string) => Promise<number>
  onChange: () => void
  onAuthorizationFailure?: () => void
}): () => void {
  let stopped = false
  let timer: number | undefined
  let pending = false
  const versionKey = `${identity}\0${scope}`
  let lastRevision: number | null = lastSeenRevisions.get(versionKey) ?? null
  let failures = 0
  const active = () => !document.hidden && navigator.onLine !== false
  const schedule = (delay: number) => {
    if (stopped || !active()) return
    window.clearTimeout(timer)
    timer = window.setTimeout(() => { void check() }, delay)
  }
  const check = async () => {
    if (stopped || pending || !active()) return
    pending = true
    const startedAt = Date.now()
    try {
      const revision = await load(scope)
      if (stopped || !active()) return
      if (lastRevision !== null && revision !== lastRevision) onChange()
      lastRevision = revision
      lastSeenRevisions.delete(versionKey)
      lastSeenRevisions.set(versionKey, revision)
      while (lastSeenRevisions.size > 128) {
        const oldest = lastSeenRevisions.keys().next().value
        if (oldest === undefined) break
        lastSeenRevisions.delete(oldest)
      }
      failures = 0
    } catch (error) {
      const status = error && typeof error === 'object' && 'status' in error ? error.status : undefined
      if (status === 401 || status === 403) {
        stop()
        onAuthorizationFailure?.()
      } else failures += 1
    } finally {
      pending = false
      if (!stopped) {
        const base = failures ? Math.min(60_000, 4_000 * 2 ** failures) : 4_000
        const cadence = base + Math.floor(200 * Math.random())
        schedule(Math.max(0, cadence - (Date.now() - startedAt)))
      }
    }
  }
  const resume = () => {
    if (active() && !pending) schedule(0)
    else window.clearTimeout(timer)
  }
  document.addEventListener('visibilitychange', resume)
  window.addEventListener('online', resume)
  window.addEventListener('offline', resume)
  schedule(0)
  function stop() {
    stopped = true
    window.clearTimeout(timer)
    document.removeEventListener('visibilitychange', resume)
    window.removeEventListener('online', resume)
    window.removeEventListener('offline', resume)
  }
  return stop
}
