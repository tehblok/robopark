import { type FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { pathForUser } from '../routes'

export function Register() {
  const { user, loading } = useAuth()
  const navigate = useNavigate()
  const [sharedPassword, setSharedPassword] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (loading) return null
  if (user) return <Navigate to={pathForUser(user)} replace />

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setSubmitting(true)

    try {
      await api.register(sharedPassword, username, password)
      navigate('/login', {
        replace: true,
        state: { registrationSuccess: true },
      })
    } catch (registrationError) {
      const status =
        registrationError instanceof Error ? registrationError.message : ''
      setError(
        status === '403'
          ? 'Invalid shared password'
          : status === '409'
            ? 'Username is already registered'
            : 'Registration failed',
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Register operator</h1>
        <label>
          Shared password
          <input
            autoComplete="off"
            autoFocus
            required
            type="password"
            value={sharedPassword}
            onChange={(event) => setSharedPassword(event.target.value)}
          />
        </label>
        <label>
          Username
          <input
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          Password
          <input
            autoComplete="new-password"
            required
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button disabled={submitting} type="submit">
          {submitting ? 'Registering…' : 'Register'}
        </button>
        <p className="form-link">
          Already registered? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </main>
  )
}
