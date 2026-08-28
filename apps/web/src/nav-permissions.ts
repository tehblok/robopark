import { ALL_NAV_ITEMS, PRIMARY_NAV_IDS, type NavId, type NavItem } from './nav'

/** Maps sidebar items to RBAC permission keys from the API. */
export const NAV_PERMISSION: Record<NavId, string> = {
  dashboard: 'nav.dashboard',
  tasks: 'nav.tasks',
  robot_search: 'nav.robot_search',
  emergency: 'nav.emergency',
  map: 'nav.map',
  analytics: 'nav.analytics',
  reports: 'nav.reports',
  learning: 'nav.learning',
  help: 'nav.help',
}

export function navItemsForPermissions(permissions: readonly string[]): NavItem[] {
  const allowed = new Set(permissions)
  return ALL_NAV_ITEMS.filter((item) => allowed.has(NAV_PERMISSION[item.id]))
}

export function hasNavPermission(permissions: readonly string[], id: NavId): boolean {
  return permissions.includes(NAV_PERMISSION[id])
}

export function primaryNavItemsFromPermissions(permissions: readonly string[]): NavItem[] {
  const all = navItemsForPermissions(permissions)
  return PRIMARY_NAV_IDS.map((id) => all.find((item) => item.id === id)).filter(
    (item): item is NavItem => item != null,
  )
}

export function moreNavItemsFromPermissions(permissions: readonly string[]): NavItem[] {
  const primary = new Set<NavId>(PRIMARY_NAV_IDS)
  return navItemsForPermissions(permissions).filter((item) => !primary.has(item.id))
}
