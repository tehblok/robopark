import { Navigate } from 'react-router-dom'
import { PageShell, Panel } from '../components/PageShell'
import { roleLabel } from '../i18n/ru'
import { useAuth } from '../auth-context'
import { hasCabinet, pathForUser } from '../routes'

export function NoCabinet() {
  const { user, loading, logout } = useAuth()

  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (hasCabinet(user)) {
    return <Navigate to={pathForUser(user)} replace />
  }

  return (
    <PageShell onLogout={logout} subtitle="Для этой роли кабинет не настроен." title="Кабинет недоступен">
      <Panel title="Детали">
        <p>
          У аккаунта роль <strong>{roleLabel(user.role)}</strong>. В текущей
          сборке для неё нет интерфейса. Попросите администратора назначить
          корректную роль.
        </p>
      </Panel>
    </PageShell>
  )
}
