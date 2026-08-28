import { type FormEvent, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Alert } from '../components/PageShell'
import { AuthBrand } from '../components/ui/AuthBrand'
import { PasswordField } from '../components/ui/PasswordField'
import { Spinner } from '../components/ui/Feedback'
import { ru } from '../i18n/ru'
import { useAuth } from '../auth-context'
import { pathForUser } from '../routes'

const LAST_USERNAME_KEY = 'robopark.lastUsername'

function readLastUsername() {
  try {
    return window.localStorage.getItem(LAST_USERNAME_KEY) ?? ''
  } catch {
    return ''
  }
}

export function Login() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState(readLastUsername)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (loading) {
    return (
      <main className="page page-auth">
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
      try {
        window.localStorage.setItem(LAST_USERNAME_KEY, username.trim())
      } catch {
        /* ignore */
      }
      navigate(pathForUser(authenticatedUser), { replace: true })
    } catch {
      setError(ru.errors.login)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page page-auth">
      <form className="login-card animate-in" onSubmit={handleSubmit}>
        <AuthBrand subtitle={ru.tagline} title={ru.brand} />
        {location.state?.registrationSuccess && (
          <Alert tone="success">Аккаунт создан. Войдите, чтобы продолжить.</Alert>
        )}
        <label className="field">
          <span className="field-label">Логин</span>
          <input
            autoComplete="username"
            autoFocus={!username}
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <PasswordField
          autoComplete="current-password"
          autoFocus={Boolean(username)}
          label="Пароль"
          onChange={setPassword}
          required
          value={password}
        />
        {error && <Alert tone="error">{error}</Alert>}
        <button className="btn" disabled={submitting} type="submit">
          {submitting ? <Spinner label="Вход…" /> : 'Войти'}
        </button>
        <p className="form-link">
          Нужен доступ? <Link to="/register">Регистрация</Link>
        </p>
      </form>
    </main>
  )
}
