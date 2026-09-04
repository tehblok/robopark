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

type RoleWithDefaults = {
  slug: string
  is_active: boolean
  permissions?: readonly string[] | null
}

export function assignableRoles<T extends RoleWithDefaults>(
  roles: readonly T[],
  actorRole: string | undefined,
): T[] {
  const active = roles.filter((role) => role.is_active)
  if (mayAssignPrivilegedPermissions(actorRole)) return active

  return active.filter((role) => {
    if (role.slug === 'admin' || role.slug === 'royal') return false
    if (!Array.isArray(role.permissions)) return false
    return !role.permissions.some((permission) => PRIVILEGED_PERMISSION_KEYS.has(permission))
  })
}
