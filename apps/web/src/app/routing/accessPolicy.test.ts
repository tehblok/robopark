import { describe, expect, it } from 'vitest'
import type { Park, User } from '../../api'
import {
  canAccessRoute,
  landingPathForUser,
  navigationForUser,
} from './accessPolicy'
import { isSystemUserRole, type AppRouteId } from './routeManifest'

const north: Park = {
  id: 7,
  name: 'North Park',
  tag: 'NORTH',
  is_active: true,
  tracker_queue: 'NORTHROBOTS',
  tracker_priority: 'normal',
  tracker_type: 'task',
  group_id: 17,
  chat_id: 27,
  feature_reports: true,
  feature_blockers: true,
  feature_sla_repair: true,
  feature_backlog_alerts: true,
}

function user(overrides: Partial<User>): User {
  return {
    id: 1,
    username: 'matrix-user',
    role: 'operator',
    access_status: 'approved',
    permissions: [],
    tracker_login: 'matrix-user',
    must_change_password: false,
    screenshot_guard: false,
    parks: [],
    ...overrides,
  }
}

const landingCases = [
  { role: 'mechanic', status: 'approved', parks: [], permissions: ['nav.dashboard'], expected: '/mechanic/no-park' },
  { role: 'operator', status: 'pending', parks: [], permissions: ['nav.dashboard'], expected: '/access/pending' },
  { role: 'operator', status: 'rejected', parks: [], permissions: ['nav.dashboard'], expected: '/access/rejected' },
  { role: 'driver', status: 'approved', parks: [], permissions: ['nav.dashboard', 'nav.robot_search', 'nav.emergency'], expected: '/overview' },
  { role: 'admin', status: 'approved', parks: [], permissions: ['nav.admin'], expected: '/admin' },
  { role: 'royal', status: 'approved', parks: [], permissions: ['nav.dashboard'], expected: '/overview' },
] as const

const protectedRoutes = [
  { id: 'work-issue', permission: 'nav.tasks', operatorOnly: false, mechanicPark: true },
  { id: 'robot-detail', permission: 'nav.robot_search', operatorOnly: false, mechanicPark: true },
  { id: 'legacy-robot-check', permission: 'nav.emergency', operatorOnly: false, mechanicPark: true },
  { id: 'overview', permission: 'nav.dashboard', operatorOnly: false, mechanicPark: true },
  { id: 'operator-parks', permission: null, operatorOnly: true, mechanicPark: false },
  { id: 'work', permission: 'nav.tasks', operatorOnly: false, mechanicPark: true },
  { id: 'robots', permission: 'nav.robot_search', operatorOnly: false, mechanicPark: true },
  { id: 'robot-check', permission: 'nav.emergency', operatorOnly: false, mechanicPark: true },
  { id: 'analytics', permission: 'nav.analytics', operatorOnly: false, mechanicPark: true },
  { id: 'reports', permission: 'nav.reports', operatorOnly: false, mechanicPark: true },
  { id: 'reports-new', permission: 'nav.reports', operatorOnly: false, mechanicPark: true },
  { id: 'report-detail', permission: 'nav.reports', operatorOnly: false, mechanicPark: true },
  { id: 'admin', permission: 'nav.admin', operatorOnly: false, mechanicPark: true },
  { id: 'admin-tracker', permission: 'nav.admin.tracker', operatorOnly: false, mechanicPark: true },
  { id: 'admin-robot-check', permission: 'nav.admin.emergency', operatorOnly: false, mechanicPark: true },
] as const
const matrixRoles = ['mechanic', 'operator', 'driver', 'admin', 'royal', 'field_lead'] as const
const matrixStatuses = ['approved', 'pending', 'rejected'] as const

const accessCases = protectedRoutes.flatMap((route) =>
  matrixRoles.flatMap((role) =>
    matrixStatuses.flatMap((status) =>
      [true, false].map((permissionPresent) => ({ route, role, status, permissionPresent })),
    ),
  ),
)

describe('canAccessRoute', () => {
  it('uses a granted management capability to expose the management hub without nav.admin', () => {
    const manager = user({
      role: 'field_lead',
      permissions: ['users.manage'],
      parks: [north],
    })

    expect(canAccessRoute(manager, 'admin')).toBe(true)
    expect(navigationForUser(manager, 'desktop').map((item) => item.id)).toContain('admin')
  })

  it('shares canonical and legacy permission gates without granting drivers Work', () => {
    const driver = user({ role: 'driver', permissions: ['nav.dashboard', 'nav.robot_search', 'nav.emergency'] })
    for (const id of ['overview', 'robots', 'robot-detail', 'robot-check', 'legacy-robot-check'] as const) {
      expect(canAccessRoute(driver, id)).toBe(true)
    }
    for (const id of ['work', 'work-issue'] as const) {
      expect(canAccessRoute(driver, id)).toBe(false)
      expect(canAccessRoute(user({ permissions: ['nav.tasks'] }), id)).toBe(true)
    }
    for (const id of ['robot-check', 'legacy-robot-check'] as const) {
      expect(canAccessRoute(user({ permissions: ['nav.robot_search'] }), id)).toBe(false)
    }
  })
  it.each(accessCases)(
    '$route.id role=$role status=$status permission=$permissionPresent',
    ({ route, role, status, permissionPresent }) => {
      const permissions = route.permission && permissionPresent ? [route.permission] : []
      const expected =
        status === 'approved' &&
        (!route.operatorOnly || role === 'operator') &&
        (!route.permission || permissionPresent)

      expect(canAccessRoute(user({ role, access_status: status, permissions, parks: [north] }), route.id))
        .toBe(expected)
    },
  )

  it.each(protectedRoutes)('denies $id while password change is required', ({ id, permission }) => {
    expect(canAccessRoute(user({
      role: 'operator',
      access_status: 'approved',
      must_change_password: true,
      permissions: permission ? [permission] : [],
      parks: [north],
    }), id)).toBe(false)
  })

  it.each(protectedRoutes)('applies mechanic park prerequisite to $id', ({ id, permission, mechanicPark }) => {
    expect(canAccessRoute(user({
      role: 'mechanic',
      access_status: 'approved',
      permissions: permission ? [permission] : [],
      parks: [],
    }), id)).toBe(!mechanicPark && id !== 'operator-parks')
  })

  it.each([
    'home',
    'login',
    'register',
    'change-password',
    'no-cabinet',
    'access-pending',
    'access-rejected',
    'mechanic-no-park',
    'not-found',
  ] as const)('keeps standalone route %s accessible outside shell policy', (id) => {
    expect(canAccessRoute(user({ access_status: 'pending', must_change_password: true }), id)).toBe(true)
  })
})

describe('landingPathForUser', () => {
  it.each(matrixRoles)('never lands %s on a parameterized route', (role) => {
    expect(landingPathForUser(user({ role, parks: [north], permissions: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency'] }))).not.toContain(':')
    expect(landingPathForUser(user({ role, parks: [north], permissions: ['nav.emergency'] }))).not.toContain(':')
  })

  it('lands drivers on static robots or no cabinet when overview is unavailable', () => {
    expect(landingPathForUser(user({ role: 'driver', permissions: ['nav.robot_search', 'nav.emergency'] }))).toBe('/robots')
    expect(landingPathForUser(user({ role: 'driver', permissions: ['nav.emergency'] }))).toBe('/no-cabinet')
  })
  it.each(landingCases)('lands $role/$status on $expected', ({ role, status, parks, permissions, expected }) => {
    expect(landingPathForUser(user({ role, access_status: status, parks: [...parks], permissions: [...permissions] })))
      .toBe(expected)
  })

  it('requires a password change before every other landing decision', () => {
    expect(landingPathForUser(user({
      role: 'mechanic',
      access_status: 'rejected',
      must_change_password: true,
      parks: [],
      permissions: ['nav.tasks'],
    }))).toBe('/change-password')
  })

  it('uses the no-cabinet fallback when no non-parameterized route is accessible', () => {
    expect(landingPathForUser(user({ role: 'royal', permissions: [] }))).toBe('/no-cabinet')
  })

  it('uses fixed system-role preferences instead of desktop manifest order', () => {
    const all = ['nav.dashboard', 'nav.tasks', 'nav.emergency', 'nav.admin']

    expect(landingPathForUser(user({ role: 'admin', parks: [north], permissions: all }))).toBe('/admin')
    expect(landingPathForUser(user({ role: 'mechanic', parks: [north], permissions: all }))).toBe('/work')
    expect(landingPathForUser(user({ role: 'driver', parks: [north], permissions: all }))).toBe('/overview')
  })

  it('falls back to permission-driven navigation for a custom role slug', () => {
    const custom = user({
      role: 'field_lead',
      access_status: 'approved',
      permissions: ['nav.dashboard', 'nav.tasks'],
      parks: [north],
    })

    expect(landingPathForUser(custom)).toBe('/overview')
    expect(navigationForUser(custom, 'mobile').map((item) => item.id).slice(0, 2))
      .toEqual(['overview', 'work'])
    expect(navigationForUser(custom, 'mobile').map((item) => item.priority))
      .toEqual([1010, 1030])
  })
})

describe('role guard', () => {
  it.each(['royal', 'admin', 'operator', 'mechanic', 'driver'])('accepts system role %s', (role) => {
    expect(isSystemUserRole(role)).toBe(true)
  })

  it.each(['field_lead', '', 'ADMIN'])('rejects custom or malformed role %s', (role) => {
    expect(isSystemUserRole(role)).toBe(false)
  })

  it('keeps route ids checked by the compiler without narrowing API role strings', () => {
    const routeId: AppRouteId = 'overview'
    const apiRole: User['role'] = 'field_lead'

    expect(canAccessRoute(user({ role: apiRole, permissions: ['nav.dashboard'], parks: [north] }), routeId))
      .toBe(true)
  })
})
