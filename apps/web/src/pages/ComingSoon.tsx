import { Link, useLocation } from 'react-router-dom'
import { PageShell } from '../components/PageShell'
import { EmptyBlock } from '../components/ui/Feedback'
import { useAuth } from '../auth-context'
import { ru } from '../i18n/ru'
import { pathForUser } from '../routes'

const SECTIONS: Record<string, { title: string; icon: string; hint: string }> = {
  '/map': {
    title: ru.nav.map,
    icon: '⊕',
    hint: 'Карта парков и роботов появится в одном из следующих релизов.',
  },
  '/learning': {
    title: ru.nav.learning,
    icon: '✦',
    hint: 'Здесь будут инструкции и обучающие материалы для механиков.',
  },
  '/help': {
    title: ru.nav.help,
    icon: '?',
    hint: 'Справка и контакты поддержки готовятся.',
  },
}

export function ComingSoon() {
  const { pathname } = useLocation()
  const { user } = useAuth()
  const section = SECTIONS[pathname] ?? {
    title: 'Раздел',
    icon: '⋯',
    hint: 'Раздел находится в разработке.',
  }

  return (
    <PageShell subtitle={ru.nav.soon} title={section.title}>
      <EmptyBlock
        action={
          user ? (
            <Link className="btn btn-secondary" to={pathForUser(user)}>
              На главную
            </Link>
          ) : undefined
        }
        hint={section.hint}
        icon={section.icon}
        title={ru.nav.soon}
      />
    </PageShell>
  )
}
