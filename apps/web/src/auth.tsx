import {
  type PropsWithChildren,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'
import { api, ApiError, ApiTimeoutError, clearApiValidators, type User } from './api'
import { AuthContext } from './auth-context'
import { pruneLegacyResourceSnapshots, resourceStore } from './lib/resource'
import { clearLegacyRecentRobots, clearProtectedBrowserStorage } from './shared/auth/protectedBrowserStorage'
import { InterfaceModeProvider } from './app/interface/InterfaceModeProvider'
import { interfaceModeStore } from './app/interface/interfaceModeStore'
import { activateDeviceResourceCache, offlineScopeForUser, purgeDeviceResourceCache, suspendDeviceResourceCache } from './lib/deviceResourceCache'
import { activeOfflineScope, purgeOfflineScope } from './pwa/offlineDb'
import { reclaimLegacyShareInbox, storageRegistry } from './pwa/storageRegistry'
import { clearShareTargetInbox, notifyOtherTabsAuthChanged } from './pwa/shareTargetStore'
import { setServiceWorkerAuthState } from './pwa/registerServiceWorker'
import { readOfflineIdentity, writeOfflineIdentity } from './shared/auth/offlineIdentity'
import type { OfflineScope } from './pwa/offlineTypes'

const LOCAL_SIGNOUT_KEY = 'robopark:local-signout:v1'
type LocalSignout = { accountId: number | null; scope: OfflineScope | null; parks: string[] }
function locallySignedOut(): LocalSignout | null {
  const empty: LocalSignout = { accountId: null, scope: null, parks: [] }
  let raw: string | null
  try { raw = localStorage.getItem(LOCAL_SIGNOUT_KEY) } catch { return null }
  try {
    if (!raw) return null
    if (raw === '1' || raw.length > 64 * 1024) return empty
    const value = JSON.parse(raw)
    const scope = value.scope
    if (!Number.isSafeInteger(value.accountId) || value.accountId <= 0
      || !scope || scope.account !== String(value.accountId) || scope.park !== 'all'
      || !['account', 'role', 'permissions', 'park'].every(key => typeof scope[key] === 'string' && scope[key].length <= 32768)
      || !['principal', 'parkAccess'].every(key => scope[key] === undefined || (typeof scope[key] === 'string' && scope[key].length <= 32768))
      || !Number.isSafeInteger(scope.schema) || scope.schema <= 0 || scope.schema > 10
      || !Array.isArray(value.parks) || value.parks.length > 257
      || !value.parks.every((park: unknown) => typeof park === 'string' && /^(all|[1-9][0-9]{0,15})$/.test(park))) return empty
    return { accountId: value.accountId, scope, parks: value.parks }
  } catch { return empty }
}
function recordLocalSignout(user: User | null): void {
  try {
    // Keep only ownership metadata needed to finish interrupted cleanup after
    // the UI identity itself has been removed. Never store draft/photo contents.
    localStorage.setItem(LOCAL_SIGNOUT_KEY, user ? JSON.stringify({
      accountId: user.id, scope: offlineScopeForUser(user, 'all'), parks: ['all', ...user.parks.map(park => String(park.id))],
    }) : '1')
  } catch { /* Local auth state remains cleared when storage is unavailable. */ }
}

function activateDraftScopes(user: User): void {
  if (user.access_status !== 'approved') return
  for (const park of ['all', ...user.parks.map(item => String(item.id))]) {
    storageRegistry.activateScope(offlineScopeForUser(user, park))
  }
}

function authorizationIdentity(user: User): string {
  return JSON.stringify([user.id, user.username, user.role, user.access_status, [...(user.permissions ?? [])].sort(), user.parks.map(item => item.id).sort((a, b) => a - b)])
}

function retireDraftScopes(user: User): void {
  for (const park of ['all', ...user.parks.map(item => String(item.id))]) {
    void storageRegistry.purgeScope(offlineScopeForUser(user, park))
  }
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [identityVerified, setIdentityVerified] = useState(false)
  const sessionGeneration = useRef(0)
  const authorizedUser = useRef<User | null>(null)
  const crossTabVerificationPending = useRef(false)
  const advanceSessionGeneration = useCallback(() => ++sessionGeneration.current, [])

  useLayoutEffect(() => {
    // UI can stop showing its spinner after a failed identity request, but a
    // network/parse/server error is not proof that the browser is anonymous.
    setServiceWorkerAuthState({ loading: loading || !identityVerified, accountId: user?.id ?? null })
    return () => setServiceWorkerAuthState(null)
  }, [loading, identityVerified, user?.id])

  const clearSessionState = useCallback(() => {
    const generation = advanceSessionGeneration()
    crossTabVerificationPending.current = false
    notifyOtherTabsAuthChanged()
    if (authorizedUser.current) retireDraftScopes(authorizedUser.current)
    authorizedUser.current = null
    const scope = activeOfflineScope()
    if (scope) void storageRegistry.purgeScope(scope)
    resourceStore.clearAll()
    clearApiValidators()
    void purgeDeviceResourceCache()
    clearProtectedBrowserStorage()
    interfaceModeStore.setAccount(null)
    setUser(null)
    setLoading(false)
    setIdentityVerified(false)
    return generation
  }, [advanceSessionGeneration])

  useEffect(() => {
    void reclaimLegacyShareInbox()
    pruneLegacyResourceSnapshots()
    clearLegacyRecentRobots()
    const generation = advanceSessionGeneration()
    // Explicit logout must survive a reload even if the offline request could
    // not revoke the server cookie. Only a new successful login clears this.
    const signout = locallySignedOut()
    if (signout) {
      const signedOutUser = readOfflineIdentity()
      const scopes = signout.scope
        ? signout.parks.map(park => ({ ...signout.scope!, park }))
        : signedOutUser ? ['all', ...signedOutUser.parks.map(park => String(park.id))].map(park => offlineScopeForUser(signedOutUser, park)) : []
      resourceStore.clearAll()
      clearApiValidators()
      clearProtectedBrowserStorage()
      interfaceModeStore.setAccount(null)
      void Promise.all([
        ...scopes.map(scope => storageRegistry.purgeScope(scope).catch(() => {})),
        purgeDeviceResourceCache().catch(() => {}),
        purgeOfflineScope().catch(() => {}),
        clearShareTargetInbox(signout.accountId ?? signedOutUser?.id ?? null).catch(() => {}),
      ]).then(() => {
        if (generation !== sessionGeneration.current) return
        setIdentityVerified(true)
        setLoading(false)
      })
      return () => { advanceSessionGeneration() }
    }
    if (navigator.onLine === false) {
      // A known disconnected device cannot complete /auth/me. Publish only a
      // validated saved identity (or the login screen) without a network timeout.
      // Keep SW activation unverified until the normal online identity check.
      crossTabVerificationPending.current = true
      const saved = readOfflineIdentity()
      // Record the prior authority before asynchronous storage work so reconnect
      // can retire it even when IndexedDB has not finished opening.
      authorizedUser.current = saved
      void (async () => {
        if (!saved) return
        await activateDeviceResourceCache(saved)
        if (generation !== sessionGeneration.current) return
        activateDraftScopes(saved)
        interfaceModeStore.setAccount(saved.id)
        authorizedUser.current = saved
        setUser(saved)
      })().catch(() => undefined).finally(() => {
        if (generation === sessionGeneration.current) setLoading(false)
      })
      return () => { advanceSessionGeneration() }
    }
    api
      .me()
      .then(async (nextUser) => {
        if (generation !== sessionGeneration.current) return
        await activateDeviceResourceCache(nextUser)
        if (generation === sessionGeneration.current) {
          activateDraftScopes(nextUser)
          interfaceModeStore.setAccount(nextUser.id)
          authorizedUser.current = nextUser
          writeOfflineIdentity(nextUser)
          setUser(nextUser)
          setIdentityVerified(true)
        }
      })
      .catch(async (error) => {
        if (generation !== sessionGeneration.current) return
        if (error instanceof ApiError && error.status === 401) {
          await Promise.all([purgeOfflineScope().catch(() => {}), clearShareTargetInbox(authorizedUser.current?.id ?? null).catch(() => {})])
          clearSessionState()
          setIdentityVerified(true)
        } else if (error instanceof TypeError || error instanceof ApiTimeoutError || error instanceof SyntaxError
          || (error instanceof ApiError && error.status >= 500)) {
          const saved = readOfflineIdentity()
          if (!saved) return
          await activateDeviceResourceCache(saved)
          if (generation !== sessionGeneration.current) return
          activateDraftScopes(saved)
          interfaceModeStore.setAccount(saved.id)
          authorizedUser.current = saved
          setUser(saved)
        }
      })
      .finally(() => {
        if (generation === sessionGeneration.current) setLoading(false)
      })
    // A generation, not a recycled mounted flag: StrictMode re-setup must
    // never authorize the previous bootstrap's response.
    return () => { advanceSessionGeneration() }
  }, [advanceSessionGeneration, clearSessionState])

  const login = async (username: string, password: string, rememberMe = false) => {
    // Any cache mirrored from a previous session (potentially a different
    // account) is dropped before we authenticate — different roles see
    // different rows and we must not paint the previous user's data.
    const previousAccountId = authorizedUser.current?.id ?? null
    const generation = clearSessionState()
    await Promise.all([purgeDeviceResourceCache(), purgeOfflineScope().catch(() => {}), clearShareTargetInbox(previousAccountId).catch(() => {})])
    await api.login(username, password, rememberMe)
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    notifyOtherTabsAuthChanged()
    const authenticatedUser = await api.me()
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    await activateDeviceResourceCache(authenticatedUser)
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    activateDraftScopes(authenticatedUser)
    interfaceModeStore.setAccount(authenticatedUser.id)
    authorizedUser.current = authenticatedUser
    try { localStorage.removeItem(LOCAL_SIGNOUT_KEY) } catch { /* Storage unavailable. */ }
    writeOfflineIdentity(authenticatedUser)
    setUser(authenticatedUser)
    setIdentityVerified(true)
    notifyOtherTabsAuthChanged('verified')
    return authenticatedUser
  }

  const refreshUser = useCallback(async () => {
    // Revalidation supersedes a pending offline bootstrap or older refresh.
    // Its late storage completion must never republish a saved identity.
    const generation = advanceSessionGeneration()
    try {
      const authenticatedUser = await api.me()
      if (generation === sessionGeneration.current) {
        const authorizationChanged = Boolean(authorizedUser.current && authorizationIdentity(authorizedUser.current) !== authorizationIdentity(authenticatedUser))
        if (authorizationChanged && authorizedUser.current) {
          retireDraftScopes(authorizedUser.current)
          await clearShareTargetInbox(authorizedUser.current.id).catch(() => {})
          if (generation !== sessionGeneration.current) return authenticatedUser
          clearProtectedBrowserStorage()
        }
        const previousScope = activeOfflineScope()
        if (previousScope && JSON.stringify(previousScope) !== JSON.stringify(offlineScopeForUser(authenticatedUser, previousScope.park))) {
          await storageRegistry.purgeScope(previousScope)
          if (generation !== sessionGeneration.current) return authenticatedUser
        }
        clearApiValidators()
        await activateDeviceResourceCache(authenticatedUser)
        if (generation === sessionGeneration.current) {
          if (authorizationChanged) resourceStore.clearAll()
          activateDraftScopes(authenticatedUser)
          interfaceModeStore.setAccount(authenticatedUser.id)
          authorizedUser.current = authenticatedUser
          crossTabVerificationPending.current = false
          writeOfflineIdentity(authenticatedUser)
          setUser(authenticatedUser)
          setIdentityVerified(true)
          if (authorizationChanged) notifyOtherTabsAuthChanged('verified')
        }
      }
      return authenticatedUser
    } catch (error) {
      if (generation === sessionGeneration.current) {
        if (error instanceof ApiError && error.status === 401) {
          const previousAccountId = authorizedUser.current?.id ?? null
          const clearedGeneration = clearSessionState()
          await Promise.all([purgeOfflineScope().catch(() => {}), clearShareTargetInbox(previousAccountId).catch(() => {})])
          if (clearedGeneration === sessionGeneration.current) setIdentityVerified(true)
        } else {
          const saved = authorizedUser.current
          if (saved) await activateDeviceResourceCache(saved)
          if (generation === sessionGeneration.current) {
            if (saved) {
              activateDraftScopes(saved)
              interfaceModeStore.setAccount(saved.id)
            }
            setUser(saved)
            setIdentityVerified(false)
          }
        }
      }
      throw error
    } finally {
      if (generation === sessionGeneration.current) setLoading(false)
    }
  }, [advanceSessionGeneration, clearSessionState])

  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.key !== 'robopark:auth-transition' || !event.newValue) return
      advanceSessionGeneration()
      suspendDeviceResourceCache()
      resourceStore.clearAll()
      clearApiValidators()
      authorizedUser.current = null
      interfaceModeStore.setAccount(null)
      setUser(null)
      setLoading(false)
      setIdentityVerified(false)
      // Never broadcast or purge another tab's newly verified durable state.
      crossTabVerificationPending.current = false
      try {
        if (JSON.parse(event.newValue).phase === 'verified') {
          crossTabVerificationPending.current = true
          void refreshUser().catch(() => undefined)
        }
      } catch { /* Older tabs send a plain nonce: invalidate only. */ }
    }
    window.addEventListener('storage', changed)
    return () => window.removeEventListener('storage', changed)
  }, [advanceSessionGeneration, refreshUser])

  useEffect(() => {
    let pending = false
    const reconnect = () => {
      if ((!authorizedUser.current && !crossTabVerificationPending.current) || pending) return
      pending = true
      void refreshUser().catch(() => undefined).finally(() => { pending = false })
    }
    window.addEventListener('online', reconnect)
    return () => window.removeEventListener('online', reconnect)
  }, [refreshUser])

  useEffect(() => {
    const authorizationFailure = (event: Event) => {
      const status = (event as CustomEvent<{ status?: number }>).detail?.status
      resourceStore.clearAll()
      void purgeDeviceResourceCache()
      // A forbidden resource is not a revoked account. Its data is purged;
      // previously verified identity alone may still restore the offline UI.
      clearProtectedBrowserStorage(undefined, { preserveOfflineIdentity: status === 403 })
      if (status === 401) {
        const previousAccountId = authorizedUser.current?.id ?? null
        clearSessionState()
        setIdentityVerified(true)
        void purgeOfflineScope().catch(() => {})
        void clearShareTargetInbox(previousAccountId).catch(() => {})
      }
    }
    window.addEventListener('robopark:authorization-failure', authorizationFailure)
    return () => window.removeEventListener('robopark:authorization-failure', authorizationFailure)
  }, [clearSessionState])

  const logout = async () => {
    const previousAccountId = authorizedUser.current?.id ?? null
    recordLocalSignout(authorizedUser.current)
    const generation = clearSessionState()
    let serverConfirmedLogout = false
    try {
      await purgeOfflineScope().catch(() => {})
      await clearShareTargetInbox(previousAccountId).catch(() => {})
      await api.logout()
      serverConfirmedLogout = true
    } finally {
      if (generation === sessionGeneration.current) {
        clearSessionState()
        if (serverConfirmedLogout) setIdentityVerified(true)
      }
    }
  }

  return (
    <AuthContext.Provider value={{ user, loading, offlineSession: Boolean(user && !loading && !identityVerified), login, refreshUser, logout }}>
      <InterfaceModeProvider accountId={user?.id ?? null}>{children}</InterfaceModeProvider>
    </AuthContext.Provider>
  )
}
