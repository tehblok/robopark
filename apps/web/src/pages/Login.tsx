import { type FormEvent, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { pathForRole } from '../routes'

export function Login() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (loading) {
    return <main className="page">Checking session…</main>
  }

  if (user) {
    return <Navigate to={pathForRole(user.role)} replace />
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setSubmitting(true)

    try {
      const authenticatedUser = await login(username, password)
      navigate(pathForRole(authenticatedUser.role), { replace: true })
    } catch {
      setError('Invalid username or password')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Robopark</h1>
        <p>Sign in to your cabinet</p>
        <label>
          Username
          <input
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="current-password"
            required
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button disabled={submitting} type="submit">
          {submitting ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </main>
  )
}
