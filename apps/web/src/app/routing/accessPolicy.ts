import type { User } from '../../api'
import {
  ROUTE_MANIFEST,
  isSystemUserRole,
  type AppRouteId,
  type NavigationItem,
  type NavSurface,
  type RouteManifestItem,
  type UserRole,
} from './routeManifest'

export type AccessUser = Pick<User,
  'role' | 'access_status' | 'permissions' | 'parks' | 'must_change_password'>

const ROLE_LANDING_PREFERENCES: Record<UserRole, readonly AppRouteId[]> = {
  royal: [
    'overview', 'admin', 'work', 'robot-check', 'robots', 'reports', 'analytics',
    'admin-tracker', 'admin-robot-check',
  ],
  admin: [
    'admin', 'overview', 'work', 'robot-check', 'robots', 'reports', 'analytics',
    'admin-tracker', 'admin-robot-check',
  ],
  operator: [
    'overview', 'operator-parks', 'work', 'robots', 'robot-check', 'reports',
    'analytics', 'admin', 'admin-tracker', 'admin-robot-check',
  ],
  mechanic: [
    'work', 'robot-check', 'robots', 'overview', 'reports', 'analytics', 'admin',
    'admin-tracker', 'admin-robot-check',
  ],
  driver: [
    'overview', 'robots', 'work', 'reports', 'analytics', 'admin',
    'admin-tracker', 'admin-robot-check',
  ],
}

function routeById(routeId: AppRouteId): RouteManifestItem {
  const route = ROUTE_MANIFEST.find((candidate) => candidate.id === routeId)
  if (!route) {
    throw new Error(`Unknown app route: ${routeId}`)
  }
  return route
}

export function canAccessRoute(user: AccessUser, routeId: AppRouteId): boolean {
  const route = routeById(routeId)
  const prerequisites = route.prerequisites ?? []

  if (prerequisites.includes('password-changed') && user.must_change_password) {
    return false
  }
  if (prerequisites.includes('approved') && user.access_status !== 'approved') {
    return false
  }
  if (
    prerequisites.includes('mechanic-has-park') &&
    user.role === 'mechanic' &&
    user.parks.length === 0
  ) {
    return false
  }
  if (routeId === 'operator-parks' && user.role !== 'operator') {
    return false
  }
  if (route.permission && !(user.permissions ?? []).includes(route.permission)) {
    return false
  }

  return true
}

function isLandingCandidate(route: RouteManifestItem): boolean {
  return route.nav != null && !route.path.includes(':')
}

export function landingPathForUser(user: AccessUser): string {
  if (user.must_change_password) {
    return routeById('change-password').path
  }
  if (user.access_status === 'pending') {
    return routeById('access-pending').path
  }
  if (user.access_status === 'rejected') {
    return routeById('access-rejected').path
  }
  if (user.role === 'mechanic' && user.parks.length === 0) {
    return routeById('mechanic-no-park').path
  }

  if (isSystemUserRole(user.role)) {
    for (const routeId of ROLE_LANDING_PREFERENCES[user.role]) {
      const route = routeById(routeId)
      if (isLandingCandidate(route) && canAccessRoute(user, routeId)) {
        return route.path
      }
    }
  } else {
    const route = ROUTE_MANIFEST
      .filter(isLandingCandidate)
      .filter((candidate) => canAccessRoute(user, candidate.id))
      .sort((left, right) => left.nav!.desktopOrder - right.nav!.desktopOrder)[0]
    if (route) {
      return route.path
    }
  }

  return routeById('no-cabinet').path
}

export function navigationForUser(user: AccessUser, surface: NavSurface): NavigationItem[] {
  return ROUTE_MANIFEST
    .filter((route): route is RouteManifestItem & { nav: NonNullable<RouteManifestItem['nav']> } =>
      route.nav != null && canAccessRoute(user, route.id),
    )
    .map((route) => {
      const priority = surface === 'desktop'
        ? route.nav.desktopOrder
        : isSystemUserRole(user.role)
          ? route.nav.mobilePriority?.[user.role] ?? 1000 + route.nav.desktopOrder
          : 1000 + route.nav.desktopOrder

      return {
        id: route.id,
        path: route.path,
        label: route.label,
        icon: route.icon,
        group: route.nav.group,
        priority,
      }
    })
    .sort((left, right) => left.priority - right.priority)
}
