import { type ReactNode } from 'react'
import { Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth-context'
import { AppShell } from './components/AppShell'
import { ParkProvider } from './park-context'
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
import { NO_CABINET_PATH, pathForUser } from './routes'

function RequirePath({
  path,
  children,
}: {
  path: string
  children: ReactNode
}) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  const userPath = pathForUser(user)
  if (userPath !== path) return <Navigate to={userPath} replace />
  return children
}

function RequirePasswordChanged({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.must_change_password) return <Navigate to="/change-password" replace />
  return children
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin' && user.role !== 'royal') {
    return <Navigate to={pathForUser(user)} replace />
  }
  return children
}

function RequireApprovedOperator({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'operator' || user.access_status !== 'approved') {
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
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'mechanic') return <Navigate to={pathForUser(user)} replace />
  const hasPark = user.parks?.length === 1
  if (requirePark && !hasPark) return <Navigate to="/mechanic/no-park" replace />
  if (!requirePark && hasPark) return <Navigate to="/dashboard" replace />
  return children
}

function canUseShell(user: NonNullable<ReturnType<typeof useAuth>['user']>): boolean {
  if (user.role === 'mechanic') return user.parks?.length === 1
  if (user.role === 'operator') return user.access_status === 'approved'
  if (user.role === 'admin' || user.role === 'royal') return true
  return false
}

function AuthenticatedShellLayout() {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (user.must_change_password) return <Navigate to="/change-password" replace />
  if (!canUseShell(user)) return <Navigate to={pathForUser(user)} replace />
  return (
    <ParkProvider>
      <AppShell />
    </ParkProvider>
  )
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
        path="/operator/pending"
        element={
          <RequirePath path="/operator/pending">
            <OperatorPending />
          </RequirePath>
        }
      />
      <Route
        path="/operator/rejected"
        element={
          <RequirePath path="/operator/rejected">
            <OperatorRejected />
          </RequirePath>
        }
      />
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
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/robots/search" element={<RobotSearch />} />
        <Route path="/emergency" element={<Emergency />} />
        <Route path="/analytics" element={<Analytics />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/map" element={<ComingSoon />} />
        <Route path="/learning" element={<ComingSoon />} />
        <Route path="/help" element={<ComingSoon />} />

        <Route
          path="/admin"
          element={
            <RequireAdmin>
              <Admin />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/tracker"
          element={
            <RequireAdmin>
              <AdminTrackerWorkspace />
            </RequireAdmin>
          }
        />
        <Route
          path="/admin/emergency/config"
          element={
            <RequireAdmin>
              <AdminEmergencyConfig />
            </RequireAdmin>
          }
        />
        </Route>
      </Route>

      {/* Legacy redirects */}
      <Route path="/operator" element={<Navigate to="/dashboard" replace />} />
      <Route path="/operator/blockers" element={<Navigate to="/tasks" replace />} />
      <Route path="/mechanic/tasks" element={<Navigate to="/tasks" replace />} />
      <Route path="/operator/robot-search" element={<Navigate to="/robots/search" replace />} />
      <Route path="/mechanic/robot-search" element={<Navigate to="/robots/search" replace />} />
      <Route path="/operator/emergency" element={<Navigate to="/emergency" replace />} />
      <Route path="/mechanic/emergency" element={<Navigate to="/emergency" replace />} />
      <Route path="/admin/emergency" element={<Navigate to="/emergency" replace />} />
      <Route path="/operator/now-report" element={<Navigate to="/analytics" replace />} />
      <Route path="/operator/tracker" element={<Navigate to="/tasks" replace />} />
      <Route path="/mechanic/tracker" element={<Navigate to="/tasks" replace />} />
      <Route path="/mechanic" element={<Navigate to="/dashboard" replace />} />

      <Route
        path="/operator/parks"
        element={
          <RequireApprovedOperator>
            <OperatorParks />
          </RequireApprovedOperator>
        }
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
