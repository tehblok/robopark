import { type ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth-context'
import { Admin } from './pages/Admin'
import { Home } from './pages/Home'
import { Login } from './pages/Login'
import { Mechanic } from './pages/Mechanic'
import { Operator } from './pages/Operator'

function RequireRole({ roles, children }: { roles: string[]; children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (!user) return <Navigate to="/login" replace />
  if (!roles.includes(user.role)) return <Navigate to="/" replace />
  return children
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route
        path="/admin"
        element={
          <RequireRole roles={['royal', 'admin']}>
            <Admin />
          </RequireRole>
        }
      />
      <Route
        path="/operator"
        element={
          <RequireRole roles={['operator']}>
            <Operator />
          </RequireRole>
        }
      />
      <Route
        path="/mechanic"
        element={
          <RequireRole roles={['mechanic']}>
            <Mechanic />
          </RequireRole>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
