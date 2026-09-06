import { AdminUsersPanel } from '../../components/admin/AdminUsersPanel'
import { Alert, PageShell } from '../../components/PageShell'
import { Spinner } from '../../components/ui/Feedback'
import { api, type Park } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { useCachedResource } from '../../lib/resource'
import { ManagementNavigation } from './ManagementNavigation'
import './management.css'

export function UserManagementPage() {
  const parks = useCachedResource<Park[]>('management:parks', () => api.parks(), {
    persist: false,
  })
  const error = parks.error ? mapApiError(parks.error, 'Не удалось загрузить парки') : ''

  return (
    <div className="rp-management"><PageShell subtitle="Роли, парки и доступы каждого участника команды." title="Пользователи">
      <ManagementNavigation />
      {error && <Alert tone="error">{error}</Alert>}
      {parks.isLoading && !parks.data ? <Spinner label="Загрузка парков…" /> : null}
      {parks.data ? <AdminUsersPanel parks={parks.data} /> : null}
    </PageShell></div>
  )
}
