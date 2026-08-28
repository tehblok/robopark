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
  /** Glyph shown in the sidebar and the mobile bottom bar. */
  icon: string
  stub?: boolean
}

export const ALL_NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', path: '/dashboard', label: ru.nav.dashboard, icon: '◧' },
  { id: 'tasks', path: '/tasks', label: ru.nav.tasks, icon: '☰' },
  { id: 'robot_search', path: '/robots/search', label: ru.nav.robot_search, icon: '⌕' },
  { id: 'emergency', path: '/emergency', label: ru.nav.emergency, icon: '⚑' },
  { id: 'map', path: '/map', label: ru.nav.map, icon: '⊕', stub: true },
  { id: 'analytics', path: '/analytics', label: ru.nav.analytics, icon: '◔' },
  { id: 'reports', path: '/reports', label: ru.nav.reports, icon: '✉' },
  { id: 'learning', path: '/learning', label: ru.nav.learning, icon: '✦', stub: true },
  { id: 'help', path: '/help', label: ru.nav.help, icon: '?', stub: true },
]

export const PRIMARY_NAV_IDS: readonly NavId[] = [
  'dashboard',
  'tasks',
  'emergency',
  'reports',
] as const

export function navItemsForRole(_role: string): NavItem[] {
  return ALL_NAV_ITEMS
}

/** @deprecated Use navItemsForPermissions from nav-permissions.ts */
export function navItemsForPermissionsLegacy(_permissions: string[]): NavItem[] {
  return ALL_NAV_ITEMS
}

export function primaryNavItems(role: string): NavItem[] {
  const all = navItemsForRole(role)
  return PRIMARY_NAV_IDS.map((id) => all.find((item) => item.id === id)).filter(
    (item): item is NavItem => item != null,
  )
}

export function moreNavItems(role: string): NavItem[] {
  const primary = new Set<NavId>(PRIMARY_NAV_IDS)
  return navItemsForRole(role).filter((item) => !primary.has(item.id))
}
