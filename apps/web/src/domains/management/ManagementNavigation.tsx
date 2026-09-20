import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../../auth-context'
import { managementHref, managementSections } from './managementSections'
import './management.css'

export function ManagementNavigation() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const items = [
    { path: '/admin', title: 'Обзор управления', href: managementHref('/admin', params) },
    ...managementSections
      .filter(section => user?.permissions?.includes(section.permission))
      .map(section => ({ ...section, href: managementHref(section.path, params, section.tab) })),
  ]
  const currentHref = items.find((item) => pathname === item.path && (
    item.path !== '/admin/settings' || ('tab' in item && item.tab ? params.get('tab') === item.tab : params.get('tab') !== 'parks')
  ))?.href ?? items[0].href
  return (
    <>
      <select
        aria-label="Раздел управления"
        className="rp-management-nav-select"
        onChange={(event) => navigate(event.target.value)}
        value={currentHref}
      >
        {items.map((item) => <option key={`${item.path}-${item.title}`} value={item.href}>{item.title}</option>)}
      </select>
      <nav aria-label="Разделы управления" className="rp-management-nav">
      <Link aria-current={pathname === '/admin' ? 'page' : undefined} to={items[0].href}>Обзор управления</Link>
      {managementSections.filter(section => user?.permissions?.includes(section.permission)).map(section => (
        <Link
          aria-current={pathname === section.path && (section.path !== '/admin/settings' || (section.tab ? params.get('tab') === section.tab : params.get('tab') !== 'parks')) ? 'page' : undefined}
          key={section.title}
          to={managementHref(section.path, params, section.tab)}
        >{section.title}</Link>
      ))}
      </nav>
    </>
  )
}
