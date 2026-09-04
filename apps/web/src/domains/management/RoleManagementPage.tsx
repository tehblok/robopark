import { AdminRolesPanel } from '../../components/admin/AdminRolesPanel'
import { PageShell } from '../../components/PageShell'
import './management.css'

export function RoleManagementPage() {
  return (
    <PageShell backTo="/admin" title="Роли и доступы">
      <AdminRolesPanel />
    </PageShell>
  )
}
