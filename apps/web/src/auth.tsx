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
import { activateDeviceResourceCache, purgeDeviceResourceCache } from './lib/deviceResourceCache'
import { purgeOfflineScope } from './pwa/offlineDb'
import { clearShareTargetInbox } from './pwa/ShareTargetInbox'

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const sessionGeneration = useRef(0)
  const advanceSessionGeneration = useCallback(() => ++sessionGeneration.current, [])

  const clearSessionState = useCallback(() => {
    const generation = advanceSessionGeneration()
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
          interfaceModeStore.setAccount(nextUser.id)
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
    interfaceModeStore.setAccount(authenticatedUser.id)
    setUser(authenticatedUser)
    return authenticatedUser
  }

  const refreshUser = useCallback(async () => {
    const generation = sessionGeneration.current
    try {
      const authenticatedUser = await api.me()
      if (generation === sessionGeneration.current) {
        clearApiValidators()
        await activateDeviceResourceCache(authenticatedUser)
        if (generation === sessionGeneration.current) {
          interfaceModeStore.setAccount(authenticatedUser.id)
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
