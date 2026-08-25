import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { MechanicTasks } from './MechanicTasks'
import { OperatorBlockers } from './OperatorBlockers'
import { pathForUser } from '../routes'

export function Tasks() {
  const { user } = useAuth()
  if (!user) return null

  switch (user.role) {
    case 'mechanic':
      return <MechanicTasks />
    case 'operator':
      return <OperatorBlockers />
    case 'admin':
    case 'royal':
      return (
        <PageShell
          subtitle="Администратор работает с тикетами через рабочий стол Tracker."
          title="Задачи"
        >
          <EmptyBlock
            action={
              <div className="actions">
                <Link className="btn" to="/admin/tracker">
                  Рабочий стол Tracker
                </Link>
                <Link className="btn btn-secondary" to="/admin">
                  Администрирование
                </Link>
              </div>
            }
            hint="Список задач по парку доступен механикам и операторам. Здесь — быстрые ссылки."
            icon="📋"
            title="Нет списка задач для этой роли"
          />
        </PageShell>
      )
    default:
      return <Navigate to={pathForUser(user)} replace />
  }
}
