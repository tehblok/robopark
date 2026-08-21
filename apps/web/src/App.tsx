import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { useAuth } from './auth-context'
import { Login } from './pages/Login'
import { pathForRole } from './routes'

function Home() {
  const { user, loading } = useAuth()

  if (loading) {
    return <main className="page">Checking session…</main>
  }

  return <Navigate to={user ? pathForRole(user.role) : '/login'} replace />
}

function Cabinet({ title }: { title: string }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <main className="page">
      <section className="cabinet">
        <h1>{title}</h1>
        <p>Signed in as {user?.username ?? 'user'}.</p>
        <button onClick={handleLogout} type="button">
          Sign out
        </button>
      </section>
    </main>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/admin" element={<Cabinet title="Admin cabinet" />} />
      <Route path="/operator" element={<Cabinet title="Operator cabinet" />} />
      <Route path="/mechanic" element={<Cabinet title="Mechanic cabinet" />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
