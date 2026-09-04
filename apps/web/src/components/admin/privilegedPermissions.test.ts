import { describe, expect, it } from 'vitest'
import { actorPermissionCatalog } from './privilegedPermissions'

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
})
