import { useAuth } from '../auth-context'

export function OperatorRejected() {
  const { logout } = useAuth()

  return (
    <main className="page">
      <section className="cabinet">
        <h1>Access rejected</h1>
        <p>Contact an administrator if your operator access should be reviewed.</p>
        <button onClick={logout} type="button">
          Sign out
        </button>
      </section>
    </main>
  )
}
