export const PRIVILEGED_PERMISSION_KEYS = new Set([
  'nav.admin',
  'nav.admin.tracker',
  'nav.admin.emergency',
  'users.manage',
  'users.approve',
  'roles.manage',
  'parks.manage',
])

export function mayAssignPrivilegedPermissions(role: string | undefined): boolean {
  return role === 'royal'
}

export function actorPermissionCatalog<T extends { key: string }>(
  catalog: T[],
  role: string | undefined,
): T[] {
  return mayAssignPrivilegedPermissions(role)
    ? catalog
    : catalog.filter((item) => !PRIVILEGED_PERMISSION_KEYS.has(item.key))
}
