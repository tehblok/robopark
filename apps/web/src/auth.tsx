import {
  type PropsWithChildren,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react'
import { api, ApiError, type User } from './api'
import { AuthContext } from './auth-context'
import { pruneLegacyResourceSnapshots, resourceStore } from './lib/resource'
import { clearProtectedBrowserStorage } from './shared/auth/protectedBrowserStorage'

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const sessionGeneration = useRef(0)
  const advanceSessionGeneration = useCallback(() => ++sessionGeneration.current, [])

  const clearSessionState = useCallback(() => {
    const generation = advanceSessionGeneration()
    resourceStore.clearAll()
    clearProtectedBrowserStorage()
    setUser(null)
    setLoading(false)
    return generation
  }, [advanceSessionGeneration])

  useEffect(() => {
    pruneLegacyResourceSnapshots()
    const generation = advanceSessionGeneration()
    api
      .me()
      .then((nextUser) => {
        if (generation === sessionGeneration.current) setUser(nextUser)
      })
      .catch((error) => {
        if (generation !== sessionGeneration.current) return
        if (error instanceof ApiError && error.status === 401) {
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
    await api.login(username, password, rememberMe)
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    const authenticatedUser = await api.me()
    if (generation !== sessionGeneration.current) throw new Error('session_changed')
    setUser(authenticatedUser)
    return authenticatedUser
  }

  const refreshUser = async () => {
    const generation = sessionGeneration.current
    try {
      const authenticatedUser = await api.me()
      if (generation === sessionGeneration.current) setUser(authenticatedUser)
      return authenticatedUser
    } catch (error) {
      if (generation === sessionGeneration.current && error instanceof ApiError && error.status === 401) {
        clearSessionState()
      }
      throw error
    }
  }

  const logout = async () => {
    const generation = clearSessionState()
    try {
      await api.logout()
    } finally {
      if (generation === sessionGeneration.current) clearSessionState()
    }
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, refreshUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
