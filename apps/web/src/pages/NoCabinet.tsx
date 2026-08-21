import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { hasCabinet, pathForUser } from '../routes'

export function NoCabinet() {
  const { user, loading, logout } = useAuth()

  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (hasCabinet(user)) {
    return <Navigate to={pathForUser(user)} replace />
  }

  return (
    <main className="page">
      <section className="cabinet">
        <h1>No cabinet</h1>
        <p>
          Your account has the role <strong>{user.role}</strong>, which has no
          cabinet in this build. Ask an administrator to correct it.
        </p>
        <button onClick={logout} type="button">
          Sign out
        </button>
      </section>
    </main>
  )
}
