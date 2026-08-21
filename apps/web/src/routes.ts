export const NO_CABINET_PATH = '/no-cabinet'

export function pathForRole(role: string) {
  switch (role) {
    case 'royal':
    case 'admin':
      return '/admin'
    case 'operator':
      return '/operator'
    case 'mechanic':
      return '/mechanic'
    default:
      // Never '/login': Login redirects here for any signed-in user, so
      // returning '/login' turns an unknown role into a redirect loop.
      return NO_CABINET_PATH
  }
}

export function hasCabinet(role: string) {
  return pathForRole(role) !== NO_CABINET_PATH
}
