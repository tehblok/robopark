import { AdminRolesPanel } from '../../components/admin/AdminRolesPanel'
import { PageShell } from '../../components/PageShell'
import { ManagementNavigation } from './ManagementNavigation'
import './management.css'
import { DomainPresentation } from '../../app/interface/DomainPresentation'

export function RoleManagementPage() {
  return (
    <div className="rp-management"><PageShell subtitle="Базовые разрешения команды и пользовательских ролей." title="Роли и доступы">
      <ManagementNavigation />
      <DomainPresentation route="admin-roles"><AdminRolesPanel /></DomainPresentation>
    </PageShell></div>
  )
}
