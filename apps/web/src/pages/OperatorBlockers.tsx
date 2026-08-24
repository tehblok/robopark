import { useEffect, useRef, useState } from 'react'
import { api, type Blocker, type Park } from '../api'
import { useAuth } from '../auth-context'
import { Alert, EmptyState, PageShell, Panel } from '../components/PageShell'
import { mapApiError } from '../i18n/errors'
import { ru, taskFilterLabel } from '../i18n/ru'

const FILTERS = [
  'all',
  'moving',
  'queued',
  'waiting_team',
  'waiting_parts',
  'other',
] as const

export function OperatorBlockers() {
  const { logout } = useAuth()
  const [parks, setParks] = useState<Park[]>([])
  const [parkId, setParkId] = useState<number | null>(null)
  const [status, setStatus] = useState('all')
  const [items, setItems] = useState<Blocker[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [parkTag, setParkTag] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const requestIdRef = useRef(0)

  useEffect(() => {
    api.operatorParks()
      .then((data) => {
        setParks(data)
        setParkId((current) => current ?? data[0]?.id ?? null)
      })
      .catch((loadError) => {
        setError(mapApiError(loadError, ru.errors.load))
        setLoading(false)
      })
  }, [])

  useEffect(() => {
    if (parkId == null) {
      setLoading(false)
      return
    }

    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    setItems([])
    setCounts({})
    setParkTag('')
    api.operatorBlockers(parkId, status)
      .then((data) => {
        if (requestId !== requestIdRef.current) return
        setItems(data.items)
        setCounts(data.counts)
        setParkTag(data.park_tag)
      })
      .catch((loadError) => {
        if (requestId !== requestIdRef.current) return
        setError(mapApiError(loadError, ru.errors.tasks))
      })
      .finally(() => {
        if (requestId !== requestIdRef.current) return
        setLoading(false)
      })
  }, [parkId, status])

  return (
    <PageShell
      backTo="/operator"
      onLogout={logout}
      subtitle={`Блокеры парка ${parkTag || '…'} · сортировка: старые сверху.`}
      title="Блокеры"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <Panel hint="Выберите парк из назначенных и фильтр по статусу блокера в Tracker." title="Парк и фильтры">
        <div className="inline-form">
          <select
            aria-label="Парк"
            disabled={!parks.length}
            onChange={(event) => setParkId(Number(event.target.value))}
            value={parkId ?? ''}
          >
            {parks.map((park) => (
              <option key={park.id} value={park.id}>
                {park.name} ({park.tag})
              </option>
            ))}
          </select>
        </div>
        {!parks.length && !error && (
          <EmptyState>Нет назначенных парков. Запросите доступ на странице «Мои парки».</EmptyState>
        )}
        {parks.length > 0 && (
          <div className="actions">
            {FILTERS.map((value) => (
              <button
                className={`btn btn-filter ${status === value ? 'is-active' : ''}`}
                key={value}
                onClick={() => setStatus(value)}
                type="button"
              >
                {taskFilterLabel(value)} ({counts[value] ?? 0})
              </button>
            ))}
          </div>
        )}
      </Panel>

      <Panel title={`Список (${items.length})`}>
        {loading && <EmptyState>{ru.loading}</EmptyState>}
        {!loading && parkId != null && !items.length && !error && (
          <EmptyState>Нет открытых blocker-ов для выбранного фильтра.</EmptyState>
        )}
        {!loading && items.length > 0 && (
          <ul className="card-list">
            {items.map((item) => (
              <li className="card" key={item.key}>
                <div className="card-title">
                  <a href={item.url} rel="noreferrer" target="_blank">{item.key}</a>
                </div>
                <p>{item.summary}</p>
                <div className="card-meta">
                  <span>Статус: {item.status}</span>
                  {item.robot && <span>Робот: {item.robot}</span>}
                  {item.hours_created && <span>В возрасте: {item.hours_created} ч</span>}
                  <span>Корзина: {taskFilterLabel(item.bucket)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </PageShell>
  )
}
