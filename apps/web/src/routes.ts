import type { User } from './api'

export const NO_CABINET_PATH = '/no-cabinet'

export function pathForUser(user: Pick<User, 'role' | 'access_status'>) {
  switch (user.role) {
    case 'royal':
    case 'admin':
      return '/admin'
    case 'operator':
      switch (user.access_status) {
        case 'pending':
          return '/operator/pending'
        case 'rejected':
          return '/operator/rejected'
        case 'approved':
          return '/operator'
        default:
          return NO_CABINET_PATH
      }
    case 'mechanic':
      return '/mechanic'
    default:
      // Never '/login': Login redirects here for any signed-in user, so
      // returning '/login' turns an unknown role into a redirect loop.
      return NO_CABINET_PATH
  }
}

export function hasCabinet(user: Pick<User, 'role' | 'access_status'>) {
  return pathForUser(user) !== NO_CABINET_PATH
}
