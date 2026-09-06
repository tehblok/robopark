import { AdminRolesPanel } from '../../components/admin/AdminRolesPanel'
import { PageShell } from '../../components/PageShell'
import { ManagementNavigation } from './ManagementNavigation'
import './management.css'

export function RoleManagementPage() {
  return (
    <div className="rp-management"><PageShell subtitle="Базовые разрешения команды и пользовательских ролей." title="Роли и доступы">
      <ManagementNavigation />
      <AdminRolesPanel />
    </PageShell></div>
  )
}
