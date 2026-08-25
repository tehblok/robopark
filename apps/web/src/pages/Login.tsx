import { type FormEvent, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Alert } from '../components/PageShell'
import { Spinner } from '../components/ui/Feedback'
import { ru } from '../i18n/ru'
import { useAuth } from '../auth-context'
import { pathForUser } from '../routes'

export function Login() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (loading) {
    return (
      <main className="page page-center">
        <Spinner label={ru.loading} />
      </main>
    )
  }

  if (user) {
    return <Navigate to={pathForUser(user)} replace />
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setSubmitting(true)

    try {
      const authenticatedUser = await login(username, password)
      navigate(pathForUser(authenticatedUser), { replace: true })
    } catch {
      setError(ru.errors.login)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page">
      <form className="login-card animate-in" onSubmit={handleSubmit}>
        <h1>{ru.brand}</h1>
        <p>{ru.tagline}</p>
        <p className="field-hint">Войдите в личный кабинет по логину и паролю.</p>
        {location.state?.registrationSuccess && (
          <Alert tone="success">Аккаунт создан. Войдите, чтобы продолжить.</Alert>
        )}
        <label className="field">
          <span className="field-label">Логин</span>
          <input
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Пароль</span>
          <input
            autoComplete="current-password"
            required
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && <Alert tone="error">{error}</Alert>}
        <button className="btn" disabled={submitting} type="submit">
          {submitting ? <Spinner label="Вход…" /> : 'Войти'}
        </button>
        <p className="form-link">
          Нужен доступ оператора? <Link to="/register">Регистрация</Link>
        </p>
      </form>
    </main>
  )
}
