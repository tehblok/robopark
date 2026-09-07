export const managementSections = [
  { permission: 'users.manage', path: '/admin/users', title: 'Пользователи', description: 'Аккаунты, роли, парки и личные доступы.' },
  { permission: 'roles.manage', path: '/admin/roles', title: 'Роли и доступы', description: 'Разрешения системных и пользовательских ролей.' },
  { permission: 'parks.manage', path: '/admin/settings', tab: 'parks', title: 'Парки', description: 'Параметры парков, заявки и политики SLA.' },
  { permission: 'nav.admin', path: '/admin/settings', title: 'Настройки', description: 'Интеграции, политики и служебные операции.' },
]

export function managementHref(path: string, params: URLSearchParams, tab?: string) {
  const next = new URLSearchParams(params)
  next.delete('tab')
  if (tab) next.set('tab', tab)
  return `${path}${next.size ? `?${next}` : ''}`
}
