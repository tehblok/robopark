import { EmptyState, Panel } from '../components/PageShell'
import { useAuth } from '../auth-context'
import { ru } from '../i18n/ru'

function analyticsCopy(role: string): string {
  switch (role) {
    case 'mechanic':
      return 'Аналитика вашего парка (каркас)'
    case 'operator':
      return 'Аналитика парков + репорты механиков (каркас)'
    case 'admin':
    case 'royal':
      return 'Сводка по паркам (каркас)'
    default:
      return 'Аналитика (каркас)'
  }
}

export function Analytics() {
  const { user } = useAuth()

  return (
    <Panel title={ru.nav.analytics}>
      <EmptyState>{analyticsCopy(user?.role ?? '')}</EmptyState>
    </Panel>
  )
}
