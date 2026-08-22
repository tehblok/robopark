import { useAuth } from '../auth-context'

export function OperatorPending() {
  const { logout } = useAuth()

  return (
    <main className="page">
      <section className="cabinet">
        <h1>Awaiting approval</h1>
        <p>An administrator must approve your operator access.</p>
        <button onClick={logout} type="button">
          Sign out
        </button>
      </section>
    </main>
  )
}
