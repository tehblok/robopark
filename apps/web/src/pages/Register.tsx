import { type FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api'
import { Alert } from '../components/PageShell'
import { AuthLayout } from '../components/auth/AuthLayout'
import { PasswordField } from '../components/ui/PasswordField'
import { RolePicker } from '../components/ui/RolePicker'
import type { RegisterRole } from '../components/ui/rolePickerModel'
import { Spinner } from '../components/ui/Feedback'
import { Button } from '../design-system/actions/Button'
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
      <AuthLayout><div className="rp-auth__card"><Spinner label={ru.loading} /></div></AuthLayout>
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
      } else if (registrationError instanceof ApiError && registrationError.status === 422) {
        setError(ru.errors.register)
      } else {
        setError(ru.errors.register)
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthLayout>
      <form className="rp-auth__card rp-auth__card--wide animate-in" onSubmit={handleSubmit}>
        <header className="rp-auth__card-head">
          <span className="rp-auth__card-kicker">Новый сотрудник</span>
          <h1>Создание аккаунта</h1>
          <p className="rp-auth__card-intro">Выберите рабочую роль и задайте данные для входа.</p>
        </header>
        <div className="rp-auth__notice">Доступ активирует владелец</div>
        <RolePicker onChange={setRoleSlug} value={roleSlug} />
        <PasswordField
          autoComplete="off"
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
        <Button busy={submitting} disabled={!checks.ok} type="submit">
          {submitting ? 'Регистрация…' : 'Зарегистрироваться'}
        </Button>
        <p className="form-link">
          Уже есть аккаунт? <Link to="/login">Войти</Link>
        </p>
      </form>
    </AuthLayout>
  )
}
