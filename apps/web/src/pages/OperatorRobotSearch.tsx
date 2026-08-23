import { PageShell } from '../components/PageShell'
import { useAuth } from '../auth-context'

export function OperatorRobotSearch() {
  const { logout } = useAuth()

  return (
    <PageShell
      backTo="/operator"
      onLogout={logout}
      title="Поиск робота"
    >
      {null}
    </PageShell>
  )
}
