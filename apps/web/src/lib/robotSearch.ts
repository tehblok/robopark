export function canSearchRobotTickets(role: string, permissions: string[]): boolean {
  if (role === 'mechanic' || role === 'operator') return true
  return (
    permissions.includes('tracker.read') ||
    permissions.includes('tracker.write') ||
    permissions.includes('nav.admin.tracker')
  )
}

export function searchPathForRole(role: string): 'mechanic' | 'operator' | 'tracker' {
  if (role === 'mechanic') return 'mechanic'
  if (role === 'operator') return 'operator'
  return 'tracker'
}
