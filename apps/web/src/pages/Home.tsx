import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { Spinner } from '../components/ui/Feedback'
import { ru } from '../i18n/ru'
import { pathForUser } from '../routes'

export function Home() {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <main className="page page-auth">
        <Spinner label={ru.loading} />
      </main>
    )
  }

  return <Navigate to={user ? pathForUser(user) : '/login'} replace />
}
