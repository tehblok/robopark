import { describe, expect, it } from 'vitest'
import { actorPermissionCatalog, assignableRoles } from './privilegedPermissions'

const catalog = [
  { key: 'nav.admin', label: 'Admin' },
  { key: 'nav.admin.tracker', label: 'Tracker' },
  { key: 'nav.admin.emergency', label: 'Emergency' },
  { key: 'users.approve', label: 'Approve' },
  { key: 'reports.resolve', label: 'Reports' },
]

describe('actorPermissionCatalog', () => {
  it('fails closed for non-owners and retains the complete catalog for royal', () => {
    expect(actorPermissionCatalog(catalog, 'operator').map((item) => item.key))
      .toEqual(['reports.resolve'])
    expect(actorPermissionCatalog(catalog, 'royal')).toEqual(catalog)
  })

  it('hides privileged identities and custom privileged defaults from non-owners', () => {
    const roles = [
      { slug: 'mechanic', is_active: true, permissions: ['reports.create'] },
      { slug: 'admin', is_active: true, permissions: [] },
      { slug: 'dispatch_lead', is_active: true, permissions: ['nav.admin'] },
      { slug: 'unknown_defaults', is_active: true },
      { slug: 'inactive', is_active: false, permissions: [] },
    ]

    expect(assignableRoles(roles, 'operator').map((role) => role.slug))
      .toEqual(['mechanic'])
    expect(assignableRoles(roles, 'royal').map((role) => role.slug))
      .toEqual(['mechanic', 'admin', 'dispatch_lead', 'unknown_defaults'])
  })
})
