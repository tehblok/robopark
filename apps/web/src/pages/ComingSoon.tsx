import { useLocation } from 'react-router-dom'
import { EmptyState, Panel } from '../components/PageShell'
import { ru } from '../i18n/ru'

const TITLES: Record<string, string> = {
  '/map': ru.nav.map,
  '/learning': ru.nav.learning,
  '/help': ru.nav.help,
}

export function ComingSoon() {
  const { pathname } = useLocation()
  const title = TITLES[pathname] ?? 'Раздел'

  return (
    <Panel title={title}>
      <EmptyState>{ru.nav.soon}</EmptyState>
    </Panel>
  )
}
