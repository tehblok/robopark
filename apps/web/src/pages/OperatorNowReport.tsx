import { PageShell } from '../components/PageShell'
import { useAuth } from '../auth-context'

export function OperatorNowReport() {
  const { logout } = useAuth()

  return (
    <PageShell
      backTo="/operator"
      onLogout={logout}
      title="Сейчас по Tracker"
    >
      {null}
    </PageShell>
  )
}
