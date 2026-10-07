import { Navigate } from 'react-router-dom'
import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'
import { TelegramAccountPanel } from '../domains/telegram/TelegramAccountPanel'
import { TelegramAccessPanel } from '../domains/telegram/TelegramAccessPanel'
import { landingPathForUser } from '../app/routing/accessPolicy'

export function OperatorPending() {
  const { user, loading, logout, refreshUser } = useAuth()

  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.access_status === 'approved') return <Navigate to={landingPathForUser(user)} replace />

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Регистрация прошла успешно."
      title="Ожидание одобрения"
    >
      <EmptyBlock
        hint="Доступ к парку одобрит его администратор или владелец платформы."
        icon="⏳"
        title="Ожидание одобрения"
      />
      {['mechanic', 'operator'].includes(user.role) ? <TelegramAccessPanel key={`access-${user.id}`} onRefresh={refreshUser} /> : null}
      <TelegramAccountPanel key={`telegram-${user.id}`} />
    </PageShell>
  )
}
