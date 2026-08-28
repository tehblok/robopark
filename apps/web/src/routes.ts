import type { User } from './api'
import { ALL_NAV_ITEMS, type NavId } from './nav'
import { NAV_PERMISSION } from './nav-permissions'

export const NO_CABINET_PATH = '/no-cabinet'

const PENDING_PATH = '/access/pending'
const REJECTED_PATH = '/access/rejected'

const HOME_NAV_ORDER: NavId[] = [
  'dashboard',
  'emergency',
  'tasks',
  'reports',
  'analytics',
  'robot_search',
  'map',
  'learning',
  'help',
]

export function pathForUser(
  user: Pick<User, 'role' | 'access_status' | 'parks' | 'must_change_password' | 'permissions'>,
) {
  if (user.must_change_password) {
    return '/change-password'
  }

  if (user.role !== 'royal') {
    if (user.access_status === 'pending') return PENDING_PATH
    if (user.access_status === 'rejected') return REJECTED_PATH
  }

  const perms = user.permissions ?? []

  if (user.role === 'mechanic' && !(user.parks?.length ?? 0)) {
    return '/mechanic/no-park'
  }

  for (const id of HOME_NAV_ORDER) {
    if (perms.includes(NAV_PERMISSION[id])) {
      const item = ALL_NAV_ITEMS.find((entry) => entry.id === id)
      if (item) return item.path
    }
  }
  if (perms.includes('nav.admin.tracker')) return '/admin/tracker'
  if (perms.includes('nav.admin')) return '/admin'

  return NO_CABINET_PATH
}

export function hasCabinet(user: Pick<User, 'role' | 'access_status' | 'parks' | 'permissions'>) {
  return pathForUser(user) !== NO_CABINET_PATH
}

export { PENDING_PATH, REJECTED_PATH }
