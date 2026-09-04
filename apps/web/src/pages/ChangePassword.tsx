import { type FormEvent, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth-context'
import { Alert, PageShell } from '../components/PageShell'
import { PasswordField } from '../components/ui/PasswordField'
import { Spinner } from '../components/ui/Feedback'
import { mapLoginError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { passwordChecks } from '../lib/passwordChecks'
import { pathForUser } from '../routes'

export function ChangePassword() {
  const { user, loading, refreshUser, logout } = useAuth()
  const navigate = useNavigate()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const checks = passwordChecks(newPassword)

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

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError('')

    if (newPassword !== confirmPassword) {
      setError('Новый пароль и подтверждение не совпадают.')
      return
    }
    if (!checks.ok) {
      setError('Пароль слишком простой: от 12 символов и три типа знаков.')
      return
    }

    setSubmitting(true)
    try {
      await api.changePassword(currentPassword, newPassword)
      const refreshed = await refreshUser()
      navigate(pathForUser(refreshed), { replace: true })
    } catch (caught) {
      setError(mapLoginError(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <PageShell
      backLabel="Вернуться в кабинет"
      backTo={user.must_change_password ? undefined : pathForUser(user)}
      onLogout={logout}
      standalone
      subtitle={user.must_change_password
        ? 'Администратор запросил смену пароля перед продолжением работы.'
        : 'Вы меняете пароль по собственной инициативе.'}
      title="Смена пароля"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <form className="login-card form-grid" onSubmit={(event) => void submit(event)}>
        <PasswordField
          autoComplete="current-password"
          autoFocus
          label="Текущий пароль"
          onChange={setCurrentPassword}
          required
          value={currentPassword}
        />
        <PasswordField
          autoComplete="new-password"
          label="Новый пароль"
          minLength={12}
          onChange={setNewPassword}
          required
          value={newPassword}
        />
        {newPassword.length > 0 && (
          <ul className="password-meter" aria-live="polite">
            <li className={checks.length ? 'is-ok' : ''}>От 12 символов</li>
            <li className={checks.classes >= 3 ? 'is-ok' : ''}>
              Три типа из четырёх: строчные, заглавные, цифры, спецсимволы
            </li>
          </ul>
        )}
        <PasswordField
          autoComplete="new-password"
          label="Подтверждение"
          onChange={setConfirmPassword}
          required
          value={confirmPassword}
        />
        <div className="form-actions">
          <button className="btn" disabled={submitting || !checks.ok} type="submit">
            {submitting ? <Spinner label="Сохранение" /> : 'Сохранить пароль'}
          </button>
        </div>
      </form>
    </PageShell>
  )
}
