import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { EmptyState, Panel } from '../components/PageShell'
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
        <Panel title="Задачи">
          <EmptyState>
            Для администратора используйте{' '}
            <Link to="/admin/tracker">рабочий стол Tracker</Link>
            {' '}или инструменты на странице{' '}
            <Link to="/admin">администрирования</Link>.
          </EmptyState>
        </Panel>
      )
    default:
      return <Navigate to={pathForUser(user)} replace />
  }
}
