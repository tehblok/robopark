import { AdminRolesPanel } from '../../components/admin/AdminRolesPanel'
import { PageShell } from '../../components/PageShell'
import { ManagementNavigation } from './ManagementNavigation'
import './management.css'
import { DomainPresentation } from '../../app/interface/DomainPresentation'

export function RoleManagementPage() {
  return (
    <div className="rp-management"><PageShell subtitle="Базовые разрешения команды и пользовательских ролей." title="Роли и доступы">
      <DomainPresentation route="admin-roles" context={<ManagementNavigation />}><AdminRolesPanel /></DomainPresentation>
    </PageShell></div>
  )
}
