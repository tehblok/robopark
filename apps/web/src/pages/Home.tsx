import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { pathForRole } from '../routes'

export function Home() {
  const { user, loading } = useAuth()

  if (loading) return null

  return <Navigate to={user ? pathForRole(user.role) : '/login'} replace />
}
