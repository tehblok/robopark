import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'

export function OperatorRejected() {
  const { logout } = useAuth()

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Доступ к кабинету закрыт."
      title="Доступ отклонён"
    >
      <EmptyBlock
        hint="Самостоятельно повторно подать заявку нельзя. Если доступ нужен — обратитесь к владельцу платформы."
        icon="⛔"
        title="Доступ отклонён"
      />
    </PageShell>
  )
}
