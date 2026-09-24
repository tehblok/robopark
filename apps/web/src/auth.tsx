import {
  type PropsWithChildren,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { api, ApiError, clearApiValidators, type User } from './api'
import { AuthContext } from './auth-context'
import { pruneLegacyResourceSnapshots, resourceStore } from './lib/resource'
import { clearProtectedBrowserStorage } from './shared/auth/protectedBrowserStorage'
import { InterfaceModeProvider } from './app/interface/InterfaceModeProvider'
import { interfaceModeStore } from './app/interface/interfaceModeStore'
import { activateDeviceResourceCache, offlineScopeForUser, purgeDeviceResourceCache } from './lib/deviceResourceCache'
import { activeOfflineScope, purgeOfflineScope } from './pwa/offlineDb'
import { storageRegistry } from './pwa/storageRegistry'
import { clearShareTargetInbox } from './pwa/ShareTargetInbox'

function activateDraftScopes(user: User): void {
  for (const park of ['all', ...user.parks.map(item => String(item.id))]) {
    storageRegistry.activateScope(offlineScopeForUser(user, park))
  }
}

function authorizationIdentity(user: User): string {
  return JSON.stringify([user.id, user.username, user.role, [...(user.permissions ?? [])].sort(), user.parks.map(item => item.id).sort((a, b) => a - b)])
}

function retireDraftScopes(user: User): void {
  for (const park of ['all', ...user.parks.map(item => String(item.id))]) {
    void storageRegistry.purgeScope(offlineScopeForUser(user, park))
  }
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const sessionGeneration = useRef(0)
  const authorizedUser = useRef<User | null>(null)
  const advanceSessionGeneration = useCallback(() => ++sessionGeneration.current, [])

  const clearSessionState = useCallback(() => {
    const generation = advanceSessionGeneration()
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
    return generation
  }, [advanceSessionGeneration])

  useEffect(() => {
    pruneLegacyResourceSnapshots()
    const generation = advanceSessionGeneration()
    api
      .me()
      .then(async (nextUser) => {
        await activateDeviceResourceCache(nextUser)
        if (generation === sessionGeneration.current) {
          activateDraftScopes(nextUser)
          interfaceModeStore.setAccount(nextUser.id)
          authorizedUser.current = nextUser
          setUser(nextUser)
        }
      })
      .catch(async (error) => {
        if (generation !== sessionGeneration.current) return
        if (error instanceof ApiError && error.status === 401) {
          await Promise.all([purgeOfflineScope().catch(() => {}), clearShareTargetInbox().catch(() => {})])
          clearSessionState()
        } else {
          setUser(null)
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
    const generation = clearSessionState()
    await Promise.all([purgeOfflineScope().catch(() => {}), clearShareTargetInbox().catch(() => {})])
    await api.login(username, password, rememberMe)
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    const authenticatedUser = await api.me()
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    await activateDeviceResourceCache(authenticatedUser)
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    activateDraftScopes(authenticatedUser)
    interfaceModeStore.setAccount(authenticatedUser.id)
    authorizedUser.current = authenticatedUser
    setUser(authenticatedUser)
    return authenticatedUser
  }

  const refreshUser = useCallback(async () => {
    const generation = sessionGeneration.current
    try {
      const authenticatedUser = await api.me()
      if (generation === sessionGeneration.current) {
        if (authorizedUser.current && authorizationIdentity(authorizedUser.current) !== authorizationIdentity(authenticatedUser)) {
          retireDraftScopes(authorizedUser.current)
        }
        const previousScope = activeOfflineScope()
        if (previousScope && JSON.stringify(previousScope) !== JSON.stringify(offlineScopeForUser(authenticatedUser, previousScope.park))) {
          await storageRegistry.purgeScope(previousScope)
        }
        clearApiValidators()
        await activateDeviceResourceCache(authenticatedUser)
        if (generation === sessionGeneration.current) {
          activateDraftScopes(authenticatedUser)
          interfaceModeStore.setAccount(authenticatedUser.id)
          authorizedUser.current = authenticatedUser
          setUser(authenticatedUser)
        }
      }
      return authenticatedUser
    } catch (error) {
      if (generation === sessionGeneration.current && error instanceof ApiError && error.status === 401) {
        await Promise.all([purgeOfflineScope().catch(() => {}), clearShareTargetInbox().catch(() => {})])
        clearSessionState()
      }
      throw error
    }
  }, [clearSessionState])

  useEffect(() => {
    const authorizationFailure = (event: Event) => {
      const status = (event as CustomEvent<{ status?: number }>).detail?.status
      resourceStore.clearAll()
      void purgeDeviceResourceCache()
      clearProtectedBrowserStorage()
      if (status === 401) {
        clearSessionState()
        void purgeOfflineScope().catch(() => {})
        void clearShareTargetInbox().catch(() => {})
      }
    }
    window.addEventListener('robopark:authorization-failure', authorizationFailure)
    return () => window.removeEventListener('robopark:authorization-failure', authorizationFailure)
  }, [clearSessionState])

  const logout = async () => {
    const generation = clearSessionState()
    try {
      await purgeOfflineScope().catch(() => {})
      await clearShareTargetInbox().catch(() => {})
      await api.logout()
    } finally {
      if (generation === sessionGeneration.current) clearSessionState()
    }
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, refreshUser, logout }}>
      <InterfaceModeProvider accountId={user?.id ?? null}>{children}</InterfaceModeProvider>
    </AuthContext.Provider>
  )
}
