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
      return '/login'
  }
}
