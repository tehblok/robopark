import { AdminUsersPanel } from '../../components/admin/AdminUsersPanel'
import { Alert, PageShell } from '../../components/PageShell'
import { Spinner } from '../../components/ui/Feedback'
import { api, type Park } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { useCachedResource } from '../../lib/resource'
import './management.css'

export function UserManagementPage() {
  const parks = useCachedResource<Park[]>('management:parks', () => api.parks(), {
    persist: false,
  })
  const error = parks.error ? mapApiError(parks.error, 'Не удалось загрузить парки') : ''

  return (
    <PageShell backTo="/admin" title="Пользователи">
      {error && <Alert tone="error">{error}</Alert>}
      {parks.isLoading && !parks.data ? <Spinner label="Загрузка парков…" /> : null}
      {parks.data ? <AdminUsersPanel parks={parks.data} /> : null}
    </PageShell>
  )
}
