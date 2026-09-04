import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { Icon } from '../../design-system/icons/Icon'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { RobotResolver, type RobotResolverApiClient } from './RobotResolver'
import { clearRecentRobots, loadRecentRobots, type RecentRobot } from './recentRobots'
import './robots.css'

export function RobotsPage({ apiClient = api }: { apiClient?: RobotResolverApiClient }) {
  const { user } = useAuth()
  if (!user) return null
  return <RobotsPageOwner apiClient={apiClient} key={user.id} userId={user.id} />
}

function RobotsPageOwner({
  apiClient,
  userId,
}: {
  apiClient: RobotResolverApiClient
  userId: number
}) {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const { parkId } = useParkScope()
  const [recent, setRecent] = useState<RecentRobot[]>(() => loadRecentRobots(userId))
  const value = searchParams.get('q') ?? ''

  const writeValue = (nextValue: string) => {
    const next = new URLSearchParams(searchParams)
    if (nextValue) next.set('q', nextValue)
    else next.delete('q')
    setSearchParams(next, { replace: true })
  }

  return (
    <PageLayout
      className="rp-robots-page"
      description="Найдите робота по номеру, VIN или ссылке на проверку."
      title="Роботы"
    >
      <Panel className="rp-robots-search-panel" title="Найти робота">
        <RobotResolver
          apiClient={apiClient}
          onResolved={(result) => {
            setRecent(loadRecentRobots(userId))
            const parkSearch = parkId == null ? '' : `?park=${parkId}`
            navigate(`/robots/${encodeURIComponent(result.vin)}${parkSearch}`, { replace: false })
          }}
          onValueChange={writeValue}
          userId={userId}
          value={value}
        />
      </Panel>

      <section className="rp-robots-scheme" aria-label="Схема робота">
        <Icon name="robot" size={56} />
        <p>Проверка начинается с подтверждения номера робота.</p>
      </section>

      <Panel
        actions={(
          <Button
            onClick={() => {
              clearRecentRobots(userId)
              setRecent([])
            }}
            type="button"
            variant="secondary"
          >
            Очистить
          </Button>
        )}
        description="Недавние роботы хранятся 30 дней на этом устройстве для текущего пользователя"
        title="Недавние роботы"
      >
        {recent.length > 0 ? (
          <ul className="rp-robots-recent-list">
            {recent.map((item) => <RecentRobotLink item={item} key={item.vin} />)}
          </ul>
        ) : <p>Недавно открытых роботов нет.</p>}
      </Panel>
    </PageLayout>
  )
}

function RecentRobotLink({ item }: { item: RecentRobot }) {
  const openedAt = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium' }).format(new Date(item.openedAt))
  return (
    <li className="rp-robots-recent-card">
      <Link to={`/robots/${encodeURIComponent(item.vin)}`}>
        <strong>{item.query}</strong>
        <span>{item.vin}</span>
        <small>Открыт {openedAt}</small>
      </Link>
    </li>
  )
}
