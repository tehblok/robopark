import { useEffect, useState } from 'react'
import { api, type Blocker } from '../api'
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

export function MechanicTasks() {
  const [status, setStatus] = useState('all')
  const [items, setItems] = useState<Blocker[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [parkTag, setParkTag] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    setError('')
    api.mechanicTasks(status)
      .then((data) => {
        setItems(data.items)
        setCounts(data.counts)
        setParkTag(data.park_tag)
      })
      .catch((loadError) => {
        setError(mapApiError(loadError, ru.errors.tasks))
      })
      .finally(() => setLoading(false))
  }, [status])

  return (
    <PageShell
      backTo="/mechanic"
      subtitle={`Блокеры парка ${parkTag || '…'} · сортировка: старые сверху.`}
      title="Задачи парка"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <Panel hint="Фильтр по статусу блокера в Tracker." title="Фильтры">
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
      </Panel>

      <Panel title={`Список (${items.length})`}>
        {loading && <EmptyState>{ru.loading}</EmptyState>}
        {!loading && !items.length && !error && (
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
