import { Link } from 'react-router-dom'
import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'
import { ru } from '../i18n/ru'
import { OperatorNowReport } from './OperatorNowReport'

function analyticsHint(role: string): string {
  switch (role) {
    case 'mechanic':
      return 'Здесь появится аналитика по вашему парку: скорость закрытия задач и повторные обращения.'
    case 'admin':
    case 'royal':
      return 'Здесь появится сводка по всем паркам: SLA, нагрузка и динамика блокеров.'
    default:
      return 'Раздел аналитики находится в разработке.'
  }
}

export function Analytics() {
  const { user } = useAuth()

  if (user?.role === 'operator') {
    return <OperatorNowReport />
  }

  return (
    <PageShell subtitle="Раздел в разработке" title={ru.nav.analytics}>
      <EmptyBlock
        action={
          <Link className="btn btn-secondary" to="/dashboard">
            Перейти на дашборд
          </Link>
        }
        hint={analyticsHint(user?.role ?? '')}
        icon="◔"
        title="Аналитика скоро появится"
      />
    </PageShell>
  )
}
