import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'

export function MechanicNoPark() {
  const { logout } = useAuth()

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Без привязки к парку инструменты недоступны."
      title="Парк не назначен"
    >
      <EmptyBlock
        hint="Администратор или владелец должен привязать хотя бы один активный парк. После назначения выйдите и войдите снова."
        icon="🏭"
        title="Парк не назначен"
      />
    </PageShell>
  )
}
