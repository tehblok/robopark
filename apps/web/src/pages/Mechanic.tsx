import { Link } from 'react-router-dom'
import { useAuth } from '../auth-context'

export function Mechanic() {
  const { logout } = useAuth()

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Mechanic</h1>
          <button onClick={logout} type="button">Sign out</button>
        </header>
        <nav className="actions">
          <Link to="/mechanic/tasks">Tasks</Link>
          <Link to="/mechanic/robot-search">Robot search</Link>
          <Link to="/mechanic/emergency">Emergency</Link>
        </nav>
      </section>
    </main>
  )
}
