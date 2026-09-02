/* eslint-disable react/only-export-components */
import type { ReactElement } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from '../shell/AppShell'
import { ParkProvider } from '../../ParkProvider'
import { Spinner } from '../../components/ui/Feedback'
import { ru } from '../../i18n/ru'
import { useAuth } from '../../auth-context'
import { Admin } from '../../pages/Admin'
import { AdminEmergencyConfig } from '../../pages/AdminEmergencyConfig'
import { AdminTrackerWorkspace } from '../../pages/AdminTrackerWorkspace'
import { Analytics } from '../../pages/Analytics'
import { ChangePassword } from '../../pages/ChangePassword'
import { Dashboard } from '../../pages/Dashboard'
import { Emergency } from '../../pages/Emergency'
import { Home } from '../../pages/Home'
import { Login } from '../../pages/Login'
import { MechanicNoPark } from '../../pages/MechanicNoPark'
import { NoCabinet } from '../../pages/NoCabinet'
import { OperatorParks } from '../../pages/OperatorParks'
import { OperatorPending } from '../../pages/OperatorPending'
import { OperatorRejected } from '../../pages/OperatorRejected'
import { Register } from '../../pages/Register'
import { Reports } from '../../pages/Reports'
import { RobotSearch } from '../../pages/RobotSearch'
import { Tasks } from '../../pages/Tasks'
import { landingPathForUser } from './accessPolicy'
import { ROUTE_MANIFEST, type AppRouteId } from './routeManifest'
import { RouteGate } from './RouteGate'

function RouteFallback() {
  return (
    <main className="page page-center">
      <Spinner label={ru.loading} />
    </main>
  )
}

export const ROUTE_ELEMENTS: Record<AppRouteId, ReactElement> = {
  'home': <Home />,
  'login': <Login />,
  'register': <Register />,
  'change-password': <ChangePassword />,
  'no-cabinet': <NoCabinet />,
  'access-pending': <OperatorPending />,
  'access-rejected': <OperatorRejected />,
  'mechanic-no-park': <MechanicNoPark />,
  'overview': <Dashboard />,
  'operator-parks': <OperatorParks />,
  'work': <Tasks />,
  'robots': <RobotSearch />,
  'robot-check': <Emergency />,
  'analytics': <Analytics />,
  'reports': <Reports />,
  'admin': <Admin />,
  'admin-tracker': <AdminTrackerWorkspace />,
  'admin-robot-check': <AdminEmergencyConfig />,
  'not-found': <RouteFallback />,
}

function LegacyRedirect({ to }: { to: string }) {
  const location = useLocation()
  return <Navigate replace to={{ pathname: to, search: location.search }} />
}

function CatchAll() {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  return <Navigate replace to={user ? landingPathForUser(user) : '/login'} />
}

function gatedElement(routeId: AppRouteId) {
  return (
    <RouteGate loadingElement={<RouteFallback />} routeId={routeId}>
      {ROUTE_ELEMENTS[routeId]}
    </RouteGate>
  )
}

export function AppRouter() {
  const publicAndStandaloneRoutes = ROUTE_MANIFEST.filter(
    (route) => route.surface !== 'shell' && route.id !== 'not-found',
  )
  const shellRoutes = ROUTE_MANIFEST.filter((route) => route.surface === 'shell')
  const legacyRoutes = ROUTE_MANIFEST.flatMap((route) =>
    (route.legacyPaths ?? []).map((path) => ({ path, canonicalPath: route.path })),
  )

  return (
    <Routes>
      {publicAndStandaloneRoutes.map((route) => (
        <Route element={gatedElement(route.id)} key={route.id} path={route.path} />
      ))}

      {legacyRoutes.map(({ path, canonicalPath }) => (
        <Route
          element={<LegacyRedirect to={canonicalPath} />}
          key={path}
          path={path}
        />
      ))}

      <Route element={<ParkProvider><AppShell /></ParkProvider>}>
        {shellRoutes.map((route) => (
          <Route element={gatedElement(route.id)} key={route.id} path={route.path} />
        ))}
      </Route>

      <Route element={<CatchAll />} path="*" />
    </Routes>
  )
}
