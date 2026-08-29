import { Navigate } from 'react-router-dom'
import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'

export function OperatorPending() {
  const { user, loading, logout } = useAuth()

  if (loading) return null
  if (!user) return <Navigate to="/login" replace />

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Регистрация прошла успешно."
      title="Ожидание одобрения"
    >
      <EmptyBlock
        hint="Владелец платформы должен одобрить доступ. Парки можно назначить при одобрении."
        icon="⏳"
        title="Ожидание одобрения"
      />
    </PageShell>
  )
}
