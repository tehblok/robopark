import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'

export function OperatorPending() {
  const { logout } = useAuth()

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
