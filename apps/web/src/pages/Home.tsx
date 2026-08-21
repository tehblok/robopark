import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { pathForUser } from '../routes'

export function Home() {
  const { user, loading } = useAuth()

  if (loading) return null

  return <Navigate to={user ? pathForUser(user) : '/login'} replace />
}
