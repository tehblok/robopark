import { type FormEvent, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Alert } from '../components/PageShell'
import { AuthLayout } from '../components/auth/AuthLayout'
import { PasswordField } from '../components/ui/PasswordField'
import { Button } from '../design-system/actions/Button'
import { Spinner } from '../components/ui/Feedback'
import { mapLoginError } from '../i18n/errors'
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
  const [rememberMe, setRememberMe] = useState(false)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (loading) {
    return (
      <AuthLayout><div className="rp-auth__card"><Spinner label={ru.loading} /></div></AuthLayout>
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
      const authenticatedUser = await login(username, password, rememberMe)
      try {
        window.localStorage.setItem(LAST_USERNAME_KEY, username.trim())
      } catch {
        /* ignore */
      }
      navigate(pathForUser(authenticatedUser), { replace: true })
    } catch (caught) {
      setError(mapLoginError(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout>
      <form className="rp-auth__card animate-in" onSubmit={handleSubmit}>
        <header className="rp-auth__card-head">
          <span className="rp-auth__card-kicker">Рабочий кабинет</span>
          <h1>{ru.auth.loginTitle}</h1>
          <p className="rp-auth__card-intro">Введите данные аккаунта, чтобы продолжить смену.</p>
        </header>
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
        <label className="field-check">
          <input
            checked={rememberMe}
            onChange={(event) => setRememberMe(event.target.checked)}
            type="checkbox"
          />
          {ru.auth.rememberMe}
        </label>
        {error && <Alert tone="error">{error}</Alert>}
        <Button busy={submitting} type="submit">{submitting ? 'Вход…' : 'Войти'}</Button>
        <p className="form-link">
          Нужен доступ? <Link to="/register">Регистрация</Link>
        </p>
      </form>
    </AuthLayout>
  )
}
