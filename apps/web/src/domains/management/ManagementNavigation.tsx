import { Link, useLocation, useSearchParams } from 'react-router-dom'
import { useAuth } from '../../auth-context'
import { managementHref, managementSections } from './managementSections'
import './management.css'

export function ManagementNavigation() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const [params] = useSearchParams()
  return (
    <nav aria-label="Разделы управления" className="rp-management-nav">
      <Link aria-current={pathname === '/admin' ? 'page' : undefined} to={managementHref('/admin', params)}>Обзор управления</Link>
      {managementSections.filter(section => user?.permissions?.includes(section.permission)).map(section => (
        <Link
          aria-current={pathname === section.path && (section.tab ? params.get('tab') === section.tab : params.get('tab') !== 'parks') ? 'page' : undefined}
          key={section.title}
          to={managementHref(section.path, params, section.tab)}
        >{section.title}</Link>
      ))}
    </nav>
  )
}
