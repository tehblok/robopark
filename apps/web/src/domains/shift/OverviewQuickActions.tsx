import { Link } from 'react-router-dom'
import type { User } from '../../api'
import { useInterfaceMode } from '../../app/interface/InterfaceModeProvider'
import { canAccessRoute } from '../../app/routing/accessPolicy'
import type { AppRouteId } from '../../app/routing/routeManifest'

type Action = { route: AppRouteId; href: string; label: string }
const robot: Action = { route: 'robots', href: '/robots', label: 'Найти робота' }
const report: Action = { route: 'reports', href: '/reports?pane=inbox', label: 'Входящие репорты' }
const actionsFor = (role: string): Action[] => {
  if (role === 'mechanic') return [
    { route: 'work', href: '/work?view=mine', label: 'Мои задачи' },
    { route: 'inventory', href: '/inventory', label: 'Найти запчасть' },
    { route: 'campaigns', href: '/campaigns', label: 'СК и оклейка' },
  ]
  if (role === 'operator') return [{ route: 'work', href: '/work?status=review', label: 'Приёмка ремонта' }, report, robot]
  if (role === 'driver') return [robot, { route: 'reports', href: '/reports', label: 'Мои репорты' }]
  return [report, { route: 'work', href: '/work', label: 'Очередь ремонта' }, { route: 'admin-users', href: '/admin/users', label: 'Команда парков' }]
}

export function OverviewQuickActions({ user, parkId }: { user: User; parkId?: number }) {
  const { mode } = useInterfaceMode()
  if (mode !== 'task-first') return null
  return <nav aria-label="Действия смены" className="a-overview-actions">
    {actionsFor(user.role).filter(action => canAccessRoute(user, action.route)).map(action => {
      const [path, search] = action.href.split('?')
      const query = new URLSearchParams(search)
      if (parkId != null) query.set('park', String(parkId))
      return <Link key={action.href} to={`${path}${query.size ? `?${query}` : ''}`}>{action.label}<span aria-hidden="true">→</span></Link>
    })}
  </nav>
}
