import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'

export function OperatorRejected() {
  const { logout } = useAuth()

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Доступ к кабинету оператора закрыт."
      title="Доступ отклонён"
    >
      <EmptyBlock
        hint="Самостоятельно повторно подать заявку нельзя. Если доступ нужен — обратитесь к администратору парка."
        icon="⛔"
        title="Доступ отклонён"
      />
    </PageShell>
  )
}
