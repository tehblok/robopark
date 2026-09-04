import { Link } from 'react-router-dom'
import { useAuth } from '../../auth-context'
import { PageShell, Panel } from '../../components/PageShell'
import './management.css'

type ManagementLink = {
  permission: string
  to: string
  title: string
  description: string
}

const links: ManagementLink[] = [
  {
    permission: 'users.manage',
    to: '/admin/users',
    title: 'Пользователи',
    description: 'Аккаунты, парки, статус доступа и принудительная смена пароля.',
  },
  {
    permission: 'roles.manage',
    to: '/admin/roles',
    title: 'Роли и доступы',
    description: 'Набор разрешений системных и пользовательских ролей.',
  },
  {
    permission: 'parks.manage',
    to: '/admin/settings?tab=parks',
    title: 'Парки',
    description: 'Настройки парков и политики SLA выбранного парка.',
  },
  {
    permission: 'nav.admin',
    to: '/admin/settings',
    title: 'Настройки',
    description: 'Интеграции, очередь и служебные операции.',
  },
]

export function ManagementPage() {
  const { user } = useAuth()
  const permissions = new Set(user?.permissions ?? [])
  const visibleLinks = links.filter((item) => permissions.has(item.permission))

  return (
    <PageShell
      subtitle="Выберите раздел управления, доступный вашему аккаунту."
      title="Управление"
    >
      <div className="management-grid">
        {visibleLinks.map((item) => (
          <Panel key={item.to} title={item.title}>
            <p className="panel-hint">{item.description}</p>
            <Link className="btn" to={item.to}>Открыть</Link>
          </Panel>
        ))}
      </div>
    </PageShell>
  )
}
