import { createContext, useContext } from 'react'
import type { User } from './api'

export type AuthContextValue = {
  user: User | null
  loading: boolean
  /** The UI was restored locally and awaits fresh server verification. */
  offlineSession?: boolean
  login: (username: string, password: string, rememberMe?: boolean) => Promise<User>
  refreshUser: () => Promise<User>
  logout: () => Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth() {
  const context = useContext(AuthContext)

  if (!context) {
    throw new Error('useAuth must be used within AuthProvider')
  }

  return context
}
