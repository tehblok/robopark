import { type FormEvent, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { Alert, PageShell } from '../components/PageShell'
import { Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { pathForUser } from '../routes'

export function ChangePassword() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  if (loading) {
    return (
      <main className="page page-center">
        <Spinner label={ru.loading} />
      </main>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  if (!user.must_change_password) {
    return <Navigate to={pathForUser(user)} replace />
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')

    if (newPassword !== confirmPassword) {
      setError('Новый пароль и подтверждение не совпадают.')
      return
    }

    setSubmitting(true)
    try {
      await api.changePassword(currentPassword, newPassword)
      const refreshed = await login(user.username, newPassword)
      navigate(pathForUser(refreshed), { replace: true })
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.generic))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <PageShell
      standalone
      subtitle="Администратор запросил смену пароля перед продолжением работы."
      title="Смена пароля"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <form className="login-card form-grid" onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span className="field-label">Текущий пароль</span>
          <input
            autoComplete="current-password"
            onChange={(event) => setCurrentPassword(event.target.value)}
            required
            type="password"
            value={currentPassword}
          />
        </label>
        <label className="field">
          <span className="field-label">Новый пароль</span>
          <input
            autoComplete="new-password"
            onChange={(event) => setNewPassword(event.target.value)}
            required
            type="password"
            value={newPassword}
          />
        </label>
        <label className="field">
          <span className="field-label">Подтверждение</span>
          <input
            autoComplete="new-password"
            onChange={(event) => setConfirmPassword(event.target.value)}
            required
            type="password"
            value={confirmPassword}
          />
        </label>
        <div className="form-actions">
          <button className="btn" disabled={submitting} type="submit">
            {submitting ? <Spinner label="Сохранение" /> : 'Сохранить пароль'}
          </button>
        </div>
      </form>
    </PageShell>
  )
}
