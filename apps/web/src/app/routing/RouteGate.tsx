import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../../auth-context'
import { canAccessRoute, landingPathForUser } from './accessPolicy'
import { ROUTE_MANIFEST, type AppRouteId } from './routeManifest'

export function RouteGate({
  routeId,
  children,
  loadingElement = null,
}: {
  routeId: AppRouteId
  children: ReactNode
  loadingElement?: ReactNode
}) {
  const { user, loading } = useAuth()
  const route = ROUTE_MANIFEST.find((candidate) => candidate.id === routeId)

  if (!route) throw new Error(`Unknown app route: ${routeId}`)
  if (route.surface === 'public') return children
  if (loading) return loadingElement
  if (!user) return <Navigate replace to="/login" />

  const landingPath = landingPathForUser(user)
  if (route.surface === 'standalone') {
    if (routeId === 'change-password') {
      return user.must_change_password || user.access_status === 'approved'
        ? children
        : <Navigate replace to={landingPath} />
    }
    return landingPath === route.path ? children : <Navigate replace to={landingPath} />
  }

  return canAccessRoute(user, routeId)
    ? children
    : <Navigate replace to={landingPath} />
}
