import { type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth-context'
import { Admin } from './pages/Admin'
import { AdminTrackerWorkspace } from './pages/AdminTrackerWorkspace'
import { Home } from './pages/Home'
import { Login } from './pages/Login'
import { Mechanic } from './pages/Mechanic'
import { MechanicEmergency } from './pages/MechanicEmergency'
import { MechanicNoPark } from './pages/MechanicNoPark'
import { MechanicRobotSearch } from './pages/MechanicRobotSearch'
import { MechanicIssueWorkspace } from './pages/MechanicIssueWorkspace'
import { MechanicTasks } from './pages/MechanicTasks'
import { NoCabinet } from './pages/NoCabinet'
import { Operator } from './pages/Operator'
import { OperatorBlockers } from './pages/OperatorBlockers'
import { OperatorIssueWorkspace } from './pages/OperatorIssueWorkspace'
import { OperatorNowReport } from './pages/OperatorNowReport'
import { OperatorPending } from './pages/OperatorPending'
import { OperatorRejected } from './pages/OperatorRejected'
import { OperatorRobotSearch } from './pages/OperatorRobotSearch'
import { Register } from './pages/Register'
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
  if (!requirePark && hasPark) return <Navigate to="/mechanic" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path={NO_CABINET_PATH} element={<NoCabinet />} />
      <Route
        path="/admin"
        element={
          <RequirePath path="/admin">
            <Admin />
          </RequirePath>
        }
      />
      <Route
        path="/admin/tracker"
        element={
          <RequirePath path="/admin">
            <AdminTrackerWorkspace />
          </RequirePath>
        }
      />
      <Route
        path="/operator"
        element={
          <RequirePath path="/operator">
            <Operator />
          </RequirePath>
        }
      />
      <Route
        path="/operator/pending"
        element={
          <RequirePath path="/operator/pending">
            <OperatorPending />
          </RequirePath>
        }
      />
      <Route
        path="/operator/blockers"
        element={
          <RequirePath path="/operator">
            <OperatorBlockers />
          </RequirePath>
        }
      />
      <Route
        path="/operator/robot-search"
        element={
          <RequirePath path="/operator">
            <OperatorRobotSearch />
          </RequirePath>
        }
      />
      <Route
        path="/operator/now-report"
        element={
          <RequirePath path="/operator">
            <OperatorNowReport />
          </RequirePath>
        }
      />
      <Route
        path="/operator/tracker"
        element={
          <RequirePath path="/operator">
            <OperatorIssueWorkspace />
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
        path="/mechanic"
        element={
          <RequireMechanic requirePark>
            <Mechanic />
          </RequireMechanic>
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
      <Route
        path="/mechanic/tasks"
        element={
          <RequireMechanic requirePark>
            <MechanicTasks />
          </RequireMechanic>
        }
      />
      <Route
        path="/mechanic/robot-search"
        element={
          <RequireMechanic requirePark>
            <MechanicRobotSearch />
          </RequireMechanic>
        }
      />
      <Route
        path="/mechanic/emergency"
        element={
          <RequireMechanic requirePark>
            <MechanicEmergency />
          </RequireMechanic>
        }
      />
      <Route
        path="/mechanic/tracker"
        element={
          <RequireMechanic requirePark>
            <MechanicIssueWorkspace />
          </RequireMechanic>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
