import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type NowReport, type Park } from '../api'
import { useAuth } from '../auth-context'
import { Alert, EmptyState, PageShell, Panel } from '../components/PageShell'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'

const TOTAL_KEYS = [
  'blocker',
  'backlog',
  'in_transit',
  'queued',
  'waiting_team',
  'waiting_parts',
  'arrived',
  'done',
] as const

const PARK_METRIC_KEYS = [
  'open_blockers',
  'backlog',
  'in_transit',
  'queued',
  'waiting_team',
  'waiting_parts',
  'arrived',
  'done',
] as const

const METRIC_LABELS: Record<string, string> = {
  blocker: 'Блокеры',
  open_blockers: 'Блокеры',
  backlog: 'Бэклог',
  in_transit: 'Перемещение',
  queued: 'В очереди',
  waiting_team: 'Смежники',
  waiting_parts: 'Запчасти',
  arrived: 'Пришло сегодня',
  done: 'Сделано сегодня',
}

const SKIP_REASON_LABELS: Record<string, string> = {
  reports_disabled: 'отчёты выключены',
  no_tracker_queue: 'нет очереди Startrek',
}

function metricLabel(key: string) {
  return METRIC_LABELS[key] ?? key
}

function skipReasonLabel(reason: string) {
  return SKIP_REASON_LABELS[reason] ?? reason
}

function formatGeneratedAt(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })
}

function MetricsGrid({ metrics }: { metrics: Record<string, number> }) {
  const keys = metrics.open_blockers != null ? PARK_METRIC_KEYS : TOTAL_KEYS

  return (
    <div className="stat-grid">
      {keys.map((key) => (
        <div className="stat" key={key}>
          <span className="stat-label">{metricLabel(key)}</span>
          <span className="stat-value">{metrics[key] ?? 0}</span>
        </div>
      ))}
    </div>
  )
}

export function OperatorNowReport() {
  const { logout } = useAuth()
  const [parks, setParks] = useState<Park[]>([])
  const [parkFilter, setParkFilter] = useState<number | null>(null)
  const [report, setReport] = useState<NowReport | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [parksLoading, setParksLoading] = useState(true)
  const requestIdRef = useRef(0)

  useEffect(() => {
    api.operatorParks()
      .then(setParks)
      .catch((loadError) => {
        setError(mapApiError(loadError, ru.errors.load))
      })
      .finally(() => setParksLoading(false))
  }, [])

  const loadReport = useCallback(async () => {
    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    setReport(null)
    try {
      const data = await api.operatorNowReport(parkFilter ?? undefined)
      if (requestId !== requestIdRef.current) return
      setReport(data)
    } catch (loadError) {
      if (requestId !== requestIdRef.current) return
      setReport(null)
      setError(mapApiError(loadError, ru.errors.load))
    } finally {
      if (requestId !== requestIdRef.current) return
      setLoading(false)
    }
  }, [parkFilter])

  useEffect(() => {
    if (parksLoading) return
    void loadReport()
  }, [loadReport, parksLoading])

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Живой срез открытых blocker по вашим паркам."
      title="Сейчас по Tracker"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <Panel hint="Выберите парк или оставьте «Все парки» для сводки по назначенным." title="Парк и обновление">
        <div className="inline-form">
          <select
            aria-label="Парк"
            disabled={!parks.length}
            onChange={(event) => {
              const value = event.target.value
              setParkFilter(value ? Number(value) : null)
            }}
            value={parkFilter ?? ''}
          >
            <option value="">Все парки</option>
            {parks.map((park) => (
              <option key={park.id} value={park.id}>
                {park.name} ({park.tag})
              </option>
            ))}
          </select>
          <button disabled={loading || !parks.length} onClick={() => void loadReport()} type="button">
            Обновить
          </button>
        </div>
        {!parks.length && !error && !parksLoading && (
          <EmptyState>Нет назначенных парков. Запросите доступ на странице «Мои парки».</EmptyState>
        )}
        {report && (
          <p className="card-meta">
            Сформировано: {formatGeneratedAt(report.generated_at)} · охват:{' '}
            {report.scope === 'park' ? 'один парк' : 'все парки'}
          </p>
        )}
      </Panel>

      <Panel title="Итого">
        {loading && <EmptyState>{ru.loading}</EmptyState>}
        {!loading && report && <MetricsGrid metrics={report.totals} />}
        {!loading && !report && !error && parks.length > 0 && (
          <EmptyState>Нажмите «Обновить», чтобы загрузить сводку.</EmptyState>
        )}
      </Panel>

      <Panel title="По паркам">
        {loading && <EmptyState>{ru.loading}</EmptyState>}
        {!loading && report && !report.parks.length && (
          <EmptyState>Нет парков с метриками для выбранного охвата.</EmptyState>
        )}
        {!loading && report && report.parks.length > 0 && (
          <ul className="card-list">
            {report.parks.map((park) => (
              <li className="card" key={park.park_id}>
                <div className="card-title">
                  {park.park_name} ({park.park_tag})
                </div>
                <MetricsGrid metrics={park.metrics} />
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {report && report.skipped_parks.length > 0 && (
        <Panel hint="Парк пропущен, если выключены отчёты или не задана очередь Tracker." title="Пропущенные парки">
          <ul className="card-list">
            {report.skipped_parks.map((park) => (
              <li className="card" key={park.park_id}>
                <div className="card-title">{park.park_name}</div>
                <div className="card-meta">
                  <span>{skipReasonLabel(park.reason)}</span>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </PageShell>
  )
}
