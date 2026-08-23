import { PageShell } from '../components/PageShell'
import { useAuth } from '../auth-context'

export function OperatorBlockers() {
  const { logout } = useAuth()

  return (
    <PageShell
      backTo="/operator"
      onLogout={logout}
      title="Блокеры"
    >
      {null}
    </PageShell>
  )
}
