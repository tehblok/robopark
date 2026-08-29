import {
  type PropsWithChildren,
  useEffect,
  useState,
} from 'react'
import { api, ApiError, type User } from './api'
import { AuthContext } from './auth-context'
import { clearRecentRobots } from './lib/recentRobots'
import { resourceStore } from './lib/resource'

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch((error) => {
        if (error instanceof ApiError && error.status === 401) {
          resourceStore.clearAll()
          clearRecentRobots()
        }
        setUser(null)
      })
      .finally(() => setLoading(false))
  }, [])

  const login = async (username: string, password: string, rememberMe = false) => {
    // Any cache mirrored from a previous session (potentially a different
    // account) is dropped before we authenticate — different roles see
    // different rows and we must not paint the previous user's data.
    resourceStore.clearAll()
    clearRecentRobots()
    await api.login(username, password, rememberMe)
    const authenticatedUser = await api.me()
    setUser(authenticatedUser)
    return authenticatedUser
  }

  const refreshUser = async () => {
    const authenticatedUser = await api.me()
    setUser(authenticatedUser)
    return authenticatedUser
  }

  const logout = async () => {
    await api.logout()
    resourceStore.clearAll()
    clearRecentRobots()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, refreshUser, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
