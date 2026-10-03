import { describe, expect, it } from 'vitest'
import type { Park, User } from '../../api'
import { reportDraftKey, reportsAccessIdentity } from './reports'

const north: Park = {
  id: 7,
  name: 'Север',
  timezone: 'Europe/Moscow',
  tag: ' north ',
  tracker_queue: ' ROBOPARK ',
  is_active: true,
}

const user: User = {
  id: 3,
  username: 'operator',
  tracker_login: 'operator.one',
  role: 'operator',
  access_status: 'approved',
  must_change_password: false,
  permissions: ['reports.resolve', 'reports.create'],
  parks: [north],
}

describe('reports owner identity', () => {
  it.each([
    ['principal', { id: 4 }],
    ['username', { username: 'renamed' }],
    ['tracker identity', { tracker_login: 'other.login' }],
    ['role', { role: 'admin' }],
    ['access state', { access_status: 'rejected' }],
    ['password gate', { must_change_password: true }],
    ['effective permissions', { permissions: ['reports.create'] }],
    ['assigned park identity', { parks: [{ ...north, tracker_queue: 'OTHER' }] }],
  ])('changes when %s changes', (_label, change) => {
    expect(reportsAccessIdentity({ ...user, ...change }, north))
      .not.toBe(reportsAccessIdentity(user, north))
  })

  it('is stable across permission and park ordering but changes with selected park', () => {
    const south = { ...north, id: 8, name: 'Юг', tag: 'south' }
    const reordered = {
      ...user,
      permissions: [...(user.permissions ?? [])].reverse(),
      parks: [south, north],
    }
    const expanded = { ...user, parks: [north, south] }

    expect(reportsAccessIdentity(reordered, north))
      .toBe(reportsAccessIdentity(expanded, north))
    expect(reportsAccessIdentity(expanded, south))
      .not.toBe(reportsAccessIdentity(expanded, north))
  })

  it('scopes non-secret drafts to the principal and selected park only', () => {
    expect(reportDraftKey(user.id, north.id)).toBe('robopark:report-draft:3:7')
  })
})
