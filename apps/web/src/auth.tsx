import {
  type PropsWithChildren,
  useEffect,
  useState,
} from 'react'
import { api, ApiError, type User } from './api'
import { AuthContext } from './auth-context'
import { resourceStore } from './lib/resource'
import { clearProtectedBrowserStorage } from './shared/auth/protectedBrowserStorage'

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const clearSessionState = () => {
    resourceStore.clearAll()
    clearProtectedBrowserStorage()
    setUser(null)
  }

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch((error) => {
        if (error instanceof ApiError && error.status === 401) {
          clearSessionState()
        } else {
          setUser(null)
        }
      })
      .finally(() => setLoading(false))
  }, [])

  const login = async (username: string, password: string, rememberMe = false) => {
    // Any cache mirrored from a previous session (potentially a different
    // account) is dropped before we authenticate — different roles see
    // different rows and we must not paint the previous user's data.
    clearSessionState()
    await api.login(username, password, rememberMe)
    const authenticatedUser = await api.me()
    setUser(authenticatedUser)
    return authenticatedUser
  }

  const refreshUser = async () => {
    try {
      const authenticatedUser = await api.me()
      setUser(authenticatedUser)
      return authenticatedUser
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearSessionState()
      }
      throw error
    }
  }

  const logout = async () => {
    try {
      await api.logout()
    } finally {
      clearSessionState()
    }
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, refreshUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
