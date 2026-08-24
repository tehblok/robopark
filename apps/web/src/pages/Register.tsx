import { type FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api'
import { Alert } from '../components/PageShell'
import { ru } from '../i18n/ru'
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
      if (registrationError instanceof ApiError && registrationError.status === 403) {
        setError(ru.errors.register403)
      } else if (registrationError instanceof ApiError && registrationError.status === 409) {
        setError(ru.errors.register409)
      } else {
        setError(ru.errors.register)
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="page">
      <form className="login-card" onSubmit={handleSubmit}>
        <h1>Регистрация оператора</h1>
        <p className="field-hint">
          После регистрации доступ будет в статусе «ожидает» — администратор
          назначит парки и одобрит вход.
        </p>
        <label className="field">
          <span className="field-label">Общий пароль</span>
          <span className="field-hint">Выдаётся администратором парка.</span>
          <input
            autoComplete="off"
            autoFocus
            required
            type="password"
            value={sharedPassword}
            onChange={(event) => setSharedPassword(event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Логин</span>
          <input
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Пароль</span>
          <input
            autoComplete="new-password"
            required
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && <Alert tone="error">{error}</Alert>}
        <button disabled={submitting} type="submit">
          {submitting ? 'Регистрация…' : 'Зарегистрироваться'}
        </button>
        <p className="form-link">
          Уже есть аккаунт? <Link to="/login">Войти</Link>
        </p>
      </form>
    </main>
  )
}
