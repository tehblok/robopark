import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api'
import { useAuth } from '../../auth-context'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { RobotResolver, type RobotResolverApiClient } from './RobotResolver'
import { checkAccessIdentity } from './robotCheckUrl'
import { clearRecentRobots, loadRecentRobots, type RecentRobot } from './recentRobots'
import './robots.css'

export function RobotsPage({ apiClient = api }: { apiClient?: RobotResolverApiClient }) {
  const { user } = useAuth()
  if (!user) return null
  return <RobotsPageOwner apiClient={apiClient} key={`${user.id}:${checkAccessIdentity(user)}`} userId={user.id} />
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
  const { parkId, selectedPark } = useParkScope()
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
      description={`Поиск и недавние роботы · ${selectedPark?.name ?? 'доступные парки'}`}
      title="Роботы"
    >
      <Panel className="rp-robots-search-panel" title="Открыть по номеру или сканировать">
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
        description="Недавние роботы хранятся 2 дня (48 часов) на этом устройстве для текущего пользователя"
        title="Недавние роботы"
      >
        {recent.length > 0 ? (
          <ul className="rp-robots-recent-list">
            {recent.map((item) => <RecentRobotLink item={item} parkId={parkId} key={item.vin} />)}
          </ul>
        ) : <p>Недавно открытых роботов нет.</p>}
      </Panel>
    </PageLayout>
  )
}

function RecentRobotLink({ item, parkId }: { item: RecentRobot; parkId: number | null }) {
  const openedAt = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium' }).format(new Date(item.openedAt))
  return (
    <li className="rp-robots-recent-card">
      <Link to={`/robots/${encodeURIComponent(item.vin)}${parkId == null ? '' : `?park=${parkId}`}`}>
        <strong>{item.query}</strong>
        <span>{item.vin}</span>
        <small>Открыт {openedAt}</small>
      </Link>
    </li>
  )
}
