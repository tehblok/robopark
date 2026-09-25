/* eslint-disable react/only-export-components */
import { Component, lazy, Suspense, type ReactElement } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from '../shell/AppShell'
import { ParkProvider } from '../../ParkProvider'
import { SyncProvider } from '../../pwa/SyncProvider'
import { Spinner } from '../../components/ui/Feedback'
import { ru } from '../../i18n/ru'
import { useAuth } from '../../auth-context'
import { ChangePassword } from '../../pages/ChangePassword'
import { Home } from '../../pages/Home'
import { Login } from '../../pages/Login'
import { MechanicNoPark } from '../../pages/MechanicNoPark'
import { NoCabinet } from '../../pages/NoCabinet'
import { OperatorParks } from '../../pages/OperatorParks'
import { OperatorPending } from '../../pages/OperatorPending'
import { OperatorRejected } from '../../pages/OperatorRejected'
import { Register } from '../../pages/Register'
import { LegacyEmergencyRedirect } from './LegacyEmergencyRedirect'
import { landingPathForUser } from './accessPolicy'
import { ROUTE_MANIFEST, type AppRouteId } from './routeManifest'
import { RouteGate } from './RouteGate'

const Admin = lazy(() => import('../../pages/Admin').then(module => ({ default: module.Admin })))
const ManagementPage = lazy(() => import('../../domains/management/ManagementPage').then(module => ({ default: module.ManagementPage })))
const UserManagementPage = lazy(() => import('../../domains/management/UserManagementPage').then(module => ({ default: module.UserManagementPage })))
const RoleManagementPage = lazy(() => import('../../domains/management/RoleManagementPage').then(module => ({ default: module.RoleManagementPage })))
const AdminEmergencyConfig = lazy(() => import('../../pages/AdminEmergencyConfig').then(module => ({ default: module.AdminEmergencyConfig })))
const Analytics = lazy(() => import('../../pages/Analytics').then(module => ({ default: module.Analytics })))
const WorkPage = lazy(() => import('../../domains/work/WorkPage').then(module => ({ default: module.WorkPage })))
const OverviewPage = lazy(() => import('../../domains/shift/OverviewPage').then(module => ({ default: module.OverviewPage })))
const SchedulePage = lazy(() => import('../../domains/shift/ScheduleWorkspace').then(module => ({ default: module.SchedulePage })))
const RobotsPage = lazy(() => import('../../domains/robots/RobotsPage').then(module => ({ default: module.RobotsPage })))
const RobotPage = lazy(() => import('../../domains/robots/RobotPage').then(module => ({ default: module.RobotPage })))
const RobotCheckPage = lazy(() => import('../../domains/robots/RobotCheckPage').then(module => ({ default: module.RobotCheckPage })))
const Reports = lazy(() => import('../../pages/Reports').then(module => ({ default: module.Reports })))
const CampaignsPage = lazy(() => import('../../domains/campaigns/CampaignsPage').then(module => ({ default: module.CampaignsPage })))
const InventoryPage = lazy(() => import('../../domains/inventory/InventoryPage').then(module => ({ default: module.InventoryPage })))
const SystemPage = lazy(() => import('../../domains/system/SystemPage').then(module => ({ default: module.SystemPage })))

function RouteFallback() {
  return (
    <main className="page page-center">
      <Spinner label={ru.loading} />
    </main>
  )
}

function RouteModuleFallback() {
  return <div className="page page-center" role="status"><Spinner label={ru.loading} /></div>
}

class RouteLoadBoundary extends Component<{ children: ReactElement }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  render() {
    if (this.state.failed) {
      return <div className="page page-center" role="alert">
        <h2>Не удалось открыть экран</h2>
        <p>Возможно, сайт обновился. Перезагрузите страницу, чтобы продолжить.</p>
        <a className="btn" href={window.location.href}>Перезагрузить страницу</a>
      </div>
    }
    return this.props.children
  }
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
  overview: <OverviewPage />,
  'operator-parks': <OperatorParks />,
  work: <WorkPage />,
  'work-issue': <WorkPage />,
  robots: <RobotsPage />,
  'robot-detail': <RobotPage />,
  'robot-check': <RobotCheckPage />,
  'legacy-robot-check': <LegacyEmergencyRedirect />,
  'analytics': <Analytics />,
  'reports': <Reports />,
  'campaigns': <CampaignsPage />,
  'campaign-detail': <CampaignsPage />,
  schedule: <SchedulePage />,
  'inventory': <InventoryPage />,
  'reports-new': <Reports />,
  'report-detail': <Reports />,
  'admin': <ManagementPage />,
  'admin-settings': <Admin />,
  'admin-users': <UserManagementPage />,
  'admin-roles': <RoleManagementPage />,
  'admin-tracker': <LegacyRedirect to="/work" />,
  'admin-robot-check': <AdminEmergencyConfig />,
  'system': <SystemPage />,
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

function ShellBoundary() {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate replace to="/login" />

  const landingPath = landingPathForUser(user)
  const landingRoute = ROUTE_MANIFEST.find((route) => route.path === landingPath)
  if (landingRoute?.surface !== 'shell') {
    return <Navigate replace to={landingPath} />
  }

  return (
    <ParkProvider>
      <SyncProvider><AppShell /></SyncProvider>
    </ParkProvider>
  )
}

function gatedElement(routeId: AppRouteId) {
  return (
    <RouteGate loadingElement={<RouteFallback />} routeId={routeId}>
      <RouteLoadBoundary>
        <Suspense fallback={<RouteModuleFallback />}>
          {ROUTE_ELEMENTS[routeId]}
        </Suspense>
      </RouteLoadBoundary>
    </RouteGate>
  )
}

export function AppRouter() {
  const publicAndStandaloneRoutes = ROUTE_MANIFEST.filter(
    (route) => route.surface !== 'shell' && route.id !== 'not-found',
  )
  const redirectRoutes = ROUTE_MANIFEST.filter((route) => route.redirectTo)
  const shellRoutes = ROUTE_MANIFEST.filter((route) => route.surface === 'shell' && !route.redirectTo)
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

      {redirectRoutes.map((route) => (
        <Route element={<LegacyRedirect to={route.redirectTo as string} />} key={route.id} path={route.path} />
      ))}

      <Route element={<ShellBoundary />}>
        {shellRoutes.map((route) => (
          <Route element={gatedElement(route.id)} key={route.id} path={route.path} />
        ))}
      </Route>

      <Route element={<CatchAll />} path="*" />
    </Routes>
  )
}
