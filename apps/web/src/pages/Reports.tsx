import { EmptyState, Panel } from '../components/PageShell'
import { useAuth } from '../auth-context'
import { ru } from '../i18n/ru'

function reportsCopy(role: string): string {
  switch (role) {
    case 'mechanic':
      return 'Ежедневные репорты механика (каркас)'
    case 'operator':
      return 'Сводка репортов по паркам (каркас)'
    case 'admin':
    case 'royal':
      return 'Цепочка репортов: механик → оператор → администратор (каркас)'
    default:
      return 'Репорты (каркас)'
  }
}

export function Reports() {
  const { user } = useAuth()

  return (
    <Panel title={ru.nav.reports}>
      <EmptyState>
        {reportsCopy(user?.role ?? '')}
        {' · '}
        Полный workflow — следующий проход
      </EmptyState>
    </Panel>
  )
}
