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
        hint="Администратор должен одобрить доступ и назначить хотя бы один активный парк. Можно выйти и зайти позже — статус сохранится."
        icon="⏳"
        title="Ожидание одобрения"
      />
    </PageShell>
  )
}
