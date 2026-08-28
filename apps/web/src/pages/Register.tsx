import { type FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api'
import { Alert } from '../components/PageShell'
import { AuthBrand } from '../components/ui/AuthBrand'
import { PasswordField } from '../components/ui/PasswordField'
import { RolePicker, type RegisterRole } from '../components/ui/RolePicker'
import { Spinner } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'
import { ru } from '../i18n/ru'
import { passwordChecks } from '../lib/passwordChecks'
import { pathForUser } from '../routes'

export function Register() {
  const { user, loading } = useAuth()
  const navigate = useNavigate()
  const [sharedPassword, setSharedPassword] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [roleSlug, setRoleSlug] = useState<RegisterRole>('operator')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const checks = passwordChecks(password)

  if (loading) {
    return (
      <main className="page page-auth">
        <Spinner label={ru.loading} />
      </main>
    )
  }
  if (user) return <Navigate to={pathForUser(user)} replace />

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    setSubmitting(true)

    try {
      await api.register(sharedPassword, username, password, roleSlug)
      navigate('/login', {
        replace: true,
        state: { registrationSuccess: true },
      })
    } catch (registrationError) {
      if (registrationError instanceof ApiError && registrationError.status === 403) {
        setError(ru.errors.register403)
      } else if (registrationError instanceof ApiError && registrationError.status === 409) {
        setError(ru.errors.register409)
      } else if (
        registrationError instanceof ApiError &&
        registrationError.status === 422
      ) {
        setError(registrationError.detail || ru.errors.register)
      } else {
        setError(ru.errors.register)
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page page-auth">
      <form className="login-card login-card-wide animate-in" onSubmit={handleSubmit}>
        <AuthBrand
          subtitle="После регистрации доступ ждёт одобрения владельца платформы."
          title="Регистрация"
        />
        <RolePicker onChange={setRoleSlug} value={roleSlug} />
        <PasswordField
          autoComplete="off"
          autoFocus
          hint="Общий пароль выдаёт владелец платформы."
          label="Общий пароль"
          onChange={setSharedPassword}
          required
          value={sharedPassword}
        />
        <label className="field">
          <span className="field-label">Логин</span>
          <input
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <PasswordField
          autoComplete="new-password"
          label="Пароль"
          minLength={12}
          onChange={setPassword}
          required
          value={password}
        />
        {password.length > 0 && (
          <ul className="password-meter" aria-live="polite">
            <li className={checks.length ? 'is-ok' : ''}>От 12 символов</li>
            <li className={checks.classes >= 3 ? 'is-ok' : ''}>
              Три типа из четырёх: строчные, заглавные, цифры, спецсимволы
            </li>
          </ul>
        )}
        {error && <Alert tone="error">{error}</Alert>}
        <button className="btn" disabled={submitting || !checks.ok} type="submit">
          {submitting ? <Spinner label="Регистрация…" /> : 'Зарегистрироваться'}
        </button>
        <p className="form-link">
          Уже есть аккаунт? <Link to="/login">Войти</Link>
        </p>
      </form>
    </main>
  )
}
