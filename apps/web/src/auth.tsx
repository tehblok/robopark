import {
  type PropsWithChildren,
  useEffect,
  useState,
} from 'react'
import { api, type User } from './api'
import { AuthContext } from './auth-context'
import { resourceStore } from './lib/resource'

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api
      .me()
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false))
  }, [])

  const login = async (username: string, password: string) => {
    // Any cache mirrored from a previous session (potentially a different
    // account) is dropped before we authenticate — different roles see
    // different rows and we must not paint the previous user's data.
    resourceStore.clearAll()
    await api.login(username, password)
    const authenticatedUser = await api.me()
    setUser(authenticatedUser)
    return authenticatedUser
  }

  const logout = async () => {
    await api.logout()
    resourceStore.clearAll()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}
