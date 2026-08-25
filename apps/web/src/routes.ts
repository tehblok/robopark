import type { User } from './api'

export const NO_CABINET_PATH = '/no-cabinet'

export function pathForUser(user: Pick<User, 'role' | 'access_status' | 'parks' | 'must_change_password'>) {
  if (user.must_change_password) {
    return '/change-password'
  }
  switch (user.role) {
    case 'royal':
    case 'admin':
      return '/dashboard'
    case 'operator':
      switch (user.access_status) {
        case 'pending':
          return '/operator/pending'
        case 'rejected':
          return '/operator/rejected'
        case 'approved':
          return '/dashboard'
        default:
          return NO_CABINET_PATH
      }
    case 'mechanic':
      if (user.parks?.length !== 1) return '/mechanic/no-park'
      return '/dashboard'
    default:
      // Never '/login': Login redirects here for any signed-in user, so
      // returning '/login' turns an unknown role into a redirect loop.
      return NO_CABINET_PATH
  }
}

export function hasCabinet(user: Pick<User, 'role' | 'access_status' | 'parks'>) {
  return pathForUser(user) !== NO_CABINET_PATH
}
