import { AdminUsersPanel } from '../../components/admin/AdminUsersPanel'
import { Alert, PageShell } from '../../components/PageShell'
import { Spinner } from '../../components/ui/Feedback'
import { api, type Park } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { useCachedResource } from '../../lib/resource'
import { useAuth } from '../../auth-context'
import { adminResourceKey, adminResourceOptions } from '../../components/admin/adminResources'
import { ManagementNavigation } from './ManagementNavigation'
import './management.css'
import { DomainPresentation } from '../../app/interface/DomainPresentation'

export function UserManagementPage() {
  const { user } = useAuth()
  const parks = useCachedResource<Park[]>(adminResourceKey('parks', user), () => api.parks(), adminResourceOptions)
  const error = parks.error ? mapApiError(parks.error, 'Не удалось загрузить парки') : ''

  return (
    <div className="rp-management"><PageShell subtitle="Роли, парки и доступы каждого участника команды." title="Пользователи">
      <ManagementNavigation />
      <DomainPresentation route="admin-users">
      {error && <Alert tone="error">{error}</Alert>}
      {parks.isLoading && !parks.data ? <Spinner label="Загрузка парков…" /> : null}
      {parks.data ? <AdminUsersPanel parks={parks.data} /> : null}
      </DomainPresentation>
    </PageShell></div>
  )
}
