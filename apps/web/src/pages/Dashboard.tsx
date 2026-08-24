import { EmptyState, Panel } from '../components/PageShell'
import { ru } from '../i18n/ru'

export function Dashboard() {
  return (
    <Panel title={ru.nav.dashboard}>
      <EmptyState>Дашборд подключается</EmptyState>
    </Panel>
  )
}
