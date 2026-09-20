import { Link, useSearchParams } from 'react-router-dom'
import { useAuth } from '../../auth-context'
import { PageShell, Panel } from '../../components/PageShell'
import { EntityRow } from '../../design-system/data/EntityRow'
import { MetricCard } from '../../design-system/data/MetricCard'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { ManagementNavigation } from './ManagementNavigation'
import { managementHref, managementSections } from './managementSections'
import './management.css'
import { DomainPresentation } from '../../app/interface/DomainPresentation'

export function ManagementPage() {
  const { user } = useAuth()
  const [params] = useSearchParams()
  const permissions = new Set(user?.permissions ?? [])
  const visibleLinks = managementSections.filter((item) => permissions.has(item.permission))

  return (
    <div className="rp-management"><PageShell
      subtitle="Доступ команды и настройки рабочего пространства."
      title="Управление"
    >
      <DomainPresentation route="admin" context={<ManagementNavigation />}>
      <div className="rp-management-metrics">
        <MetricCard label="Доступные разделы" value={visibleLinks.length} />
        <MetricCard label="Назначенные парки" value={user?.parks.length ?? 0} />
      </div>
      <Panel title="Разделы управления" hint="Выберите направление работы.">
      <div className="management-grid">
        {visibleLinks.map((item) => (
          <EntityRow key={item.title} title={item.title} meta={item.description}
            status={<StatusBadge tone="success">Доступен</StatusBadge>}
            actions={<Link className="btn btn-secondary" to={managementHref(item.path, params, item.tab)}>Открыть</Link>} />
        ))}
      </div>
      </Panel>
      </DomainPresentation>
    </PageShell></div>
  )
}
