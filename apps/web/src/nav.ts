import { ru } from './i18n/ru'

export type NavId =
  | 'dashboard'
  | 'tasks'
  | 'robot_search'
  | 'emergency'
  | 'map'
  | 'analytics'
  | 'reports'
  | 'learning'
  | 'help'

export type NavItem = {
  id: NavId
  path: string
  label: string
  stub?: boolean
}

const ALL_NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', path: '/dashboard', label: ru.nav.dashboard },
  { id: 'tasks', path: '/tasks', label: ru.nav.tasks },
  { id: 'robot_search', path: '/robots/search', label: ru.nav.robot_search },
  { id: 'emergency', path: '/emergency', label: ru.nav.emergency },
  { id: 'map', path: '/map', label: ru.nav.map, stub: true },
  { id: 'analytics', path: '/analytics', label: ru.nav.analytics },
  { id: 'reports', path: '/reports', label: ru.nav.reports },
  { id: 'learning', path: '/learning', label: ru.nav.learning, stub: true },
  { id: 'help', path: '/help', label: ru.nav.help, stub: true },
]

export function navItemsForRole(_role: string): NavItem[] {
  return ALL_NAV_ITEMS
}
