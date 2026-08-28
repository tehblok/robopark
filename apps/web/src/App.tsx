import { type ReactNode } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth-context'
import { AppShell } from './components/AppShell'
import { Spinner } from './components/ui/Feedback'
import { ParkProvider } from './ParkProvider'
import { Admin } from './pages/Admin'
import { AdminEmergencyConfig } from './pages/AdminEmergencyConfig'
import { AdminTrackerWorkspace } from './pages/AdminTrackerWorkspace'
import { Analytics } from './pages/Analytics'
import { ChangePassword } from './pages/ChangePassword'
import { ComingSoon } from './pages/ComingSoon'
import { Dashboard } from './pages/Dashboard'
import { Emergency } from './pages/Emergency'
import { Home } from './pages/Home'
import { Login } from './pages/Login'
import { MechanicNoPark } from './pages/MechanicNoPark'
import { NoCabinet } from './pages/NoCabinet'
import { OperatorParks } from './pages/OperatorParks'
import { OperatorPending } from './pages/OperatorPending'
import { OperatorRejected } from './pages/OperatorRejected'
import { Register } from './pages/Register'
import { Reports } from './pages/Reports'
import { RobotSearch } from './pages/RobotSearch'
import { Tasks } from './pages/Tasks'
import { ru } from './i18n/ru'
import { NO_CABINET_PATH, PENDING_PATH, REJECTED_PATH, pathForUser } from './routes'

function RouteFallback() {
  return (
    <main className="page page-center">
      <Spinner label={ru.loading} />
    </main>
  )
}

function RequirePath({
  path,
  children,
}: {
  path: string
  children: ReactNode
}) {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate to="/login" replace />
  const userPath = pathForUser(user)
  if (userPath !== path) return <Navigate to={userPath} replace />
  return children
}

function RequirePasswordChanged({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate to="/login" replace />
  if (user.must_change_password) return <Navigate to="/change-password" replace />
  return children
}

function RequirePermission({
  permission,
  children,
}: {
  permission: string
  children: ReactNode
}) {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate to="/login" replace />
  if (!(user.permissions ?? []).includes(permission)) {
    return <Navigate to={pathForUser(user)} replace />
  }
  return children
}

function RequireMechanic({
  children,
  requirePark = true,
}: {
  children: ReactNode
  requirePark?: boolean
}) {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'mechanic') return <Navigate to={pathForUser(user)} replace />
  const hasPark = (user.parks?.length ?? 0) >= 1
  if (requirePark && !hasPark) return <Navigate to="/mechanic/no-park" replace />
  if (!requirePark && hasPark) return <Navigate to="/dashboard" replace />
  return children
}

function RequireApprovedOperator({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate to="/login" replace />
  if (user.must_change_password) return <Navigate to="/change-password" replace />
  if (user.role !== 'operator' || user.access_status !== 'approved') {
    return <Navigate to={pathForUser(user)} replace />
  }
  return children
}

function CatchAll() {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate to="/login" replace />
  return <Navigate to={pathForUser(user)} replace />
}

function canUseShell(user: NonNullable<ReturnType<typeof useAuth>['user']>): boolean {
  if (user.role !== 'royal') {
    if (user.access_status === 'pending' || user.access_status === 'rejected') return false
  }
  if (user.role === 'mechanic') return (user.parks?.length ?? 0) >= 1
  if (user.role === 'driver') return user.access_status === 'approved'
  return (user.permissions?.length ?? 0) > 0
}

function AuthenticatedShellLayout() {
  const { user, loading } = useAuth()
  if (loading) return <RouteFallback />
  if (!user) return <Navigate to="/login" replace />
  if (user.must_change_password) return <Navigate to="/change-password" replace />
  if (!canUseShell(user)) return <Navigate to={pathForUser(user)} replace />
  const shell = (
    <>
      <AppShell />
    </>
  )
  return <ParkProvider>{shell}</ParkProvider>
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/change-password" element={<ChangePassword />} />
      <Route path={NO_CABINET_PATH} element={<NoCabinet />} />

      <Route
        path={PENDING_PATH}
        element={
          <RequirePath path={PENDING_PATH}>
            <OperatorPending />
          </RequirePath>
        }
      />
      <Route
        path={REJECTED_PATH}
        element={
          <RequirePath path={REJECTED_PATH}>
            <OperatorRejected />
          </RequirePath>
        }
      />
      <Route path="/operator/pending" element={<Navigate to={PENDING_PATH} replace />} />
      <Route path="/operator/rejected" element={<Navigate to={REJECTED_PATH} replace />} />

      <Route
        path="/mechanic/no-park"
        element={
          <RequireMechanic requirePark={false}>
            <MechanicNoPark />
          </RequireMechanic>
        }
      />

      <Route element={<AuthenticatedShellLayout />}>
        <Route
          element={
            <RequirePasswordChanged>
              <Outlet />
            </RequirePasswordChanged>
          }
        >
        <Route
          path="/dashboard"
          element={
            <RequirePermission permission="nav.dashboard">
              <Dashboard />
            </RequirePermission>
          }
        />
        <Route
          path="/tasks"
          element={
            <RequirePermission permission="nav.tasks">
              <Tasks />
            </RequirePermission>
          }
        />
        <Route
          path="/robots/search"
          element={
            <RequirePermission permission="nav.robot_search">
              <RobotSearch />
            </RequirePermission>
          }
        />
        <Route
          path="/emergency"
          element={
            <RequirePermission permission="nav.emergency">
              <Emergency />
            </RequirePermission>
          }
        />
        <Route
          path="/analytics"
          element={
            <RequirePermission permission="nav.analytics">
              <Analytics />
            </RequirePermission>
          }
        />
        <Route
          path="/reports"
          element={
            <RequirePermission permission="nav.reports">
              <Reports />
            </RequirePermission>
          }
        />
        <Route
          path="/map"
          element={
            <RequirePermission permission="nav.map">
              <ComingSoon />
            </RequirePermission>
          }
        />
        <Route
          path="/learning"
          element={
            <RequirePermission permission="nav.learning">
              <ComingSoon />
            </RequirePermission>
          }
        />
        <Route
          path="/help"
          element={
            <RequirePermission permission="nav.help">
              <ComingSoon />
            </RequirePermission>
          }
        />

        <Route
          path="/admin"
          element={
            <RequirePermission permission="nav.admin">
              <Admin />
            </RequirePermission>
          }
        />
        <Route
          path="/admin/tracker"
          element={
            <RequirePermission permission="nav.admin.tracker">
              <AdminTrackerWorkspace />
            </RequirePermission>
          }
        />
        <Route
          path="/admin/emergency/config"
          element={
            <RequirePermission permission="nav.admin.emergency">
              <AdminEmergencyConfig />
            </RequirePermission>
          }
        />
        </Route>
      </Route>

      <Route path="/operator" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/operator/parks"
        element={
          <RequireApprovedOperator>
            <OperatorParks />
          </RequireApprovedOperator>
        }
      />
      <Route path="*" element={<CatchAll />} />
    </Routes>
  )
}
