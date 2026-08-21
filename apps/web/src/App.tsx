import { type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth-context'
import { Admin } from './pages/Admin'
import { Home } from './pages/Home'
import { Login } from './pages/Login'
import { Mechanic } from './pages/Mechanic'
import { NoCabinet } from './pages/NoCabinet'
import { Operator } from './pages/Operator'
import { OperatorPending } from './pages/OperatorPending'
import { OperatorRejected } from './pages/OperatorRejected'
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
          <RequirePath path="/mechanic">
            <Mechanic />
          </RequirePath>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
