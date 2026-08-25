import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type NowReport, type Park } from '../api'
import { Alert, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonKpi, SkeletonList, Spinner } from '../components/ui/Feedback'
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

function metricTone(key: string): string {
  if (key === 'done' || key === 'arrived') return 'tone-done'
  if (key === 'queued' || key === 'in_transit' || key === 'waiting_team' || key === 'waiting_parts') {
    return 'tone-queued'
  }
  if (key === 'blocker' || key === 'open_blockers') return 'tone-blocker'
  return ''
}

function formatGeneratedAt(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })
}

function MetricsGrid({ metrics }: { metrics: Record<string, number> }) {
  const keys = metrics.open_blockers != null ? PARK_METRIC_KEYS : TOTAL_KEYS

  return (
    <div className="dashboard-kpi-grid analytics-kpi-grid">
      {keys.map((key) => (
        <div className={`dashboard-kpi ${metricTone(key)}`.trim()} key={key}>
          <span className="dashboard-kpi-value">{metrics[key] ?? 0}</span>
          <span className="dashboard-kpi-label">{metricLabel(key)}</span>
        </div>
      ))}
    </div>
  )
}

/** Live Tracker snapshot for operators — shown under /analytics. */
export function OperatorNowReport() {
  const [parks, setParks] = useState<Park[]>([])
  const [parkFilter, setParkFilter] = useState<number | null>(null)
  const [report, setReport] = useState<NowReport | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [parksLoading, setParksLoading] = useState(true)
  const requestIdRef = useRef(0)

  useEffect(() => {
    let cancelled = false
    api
      .operatorParks()
      .then((data) => {
        if (!cancelled) setParks(data)
      })
      .catch((loadError) => {
        if (!cancelled) setError(mapApiError(loadError, ru.errors.load))
      })
      .finally(() => {
        if (!cancelled) setParksLoading(false)
      })
    return () => {
      cancelled = true
    }
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
      if (requestId === requestIdRef.current) {
        setLoading(false)
      }
    }
  }, [parkFilter])

  useEffect(() => {
    if (parksLoading) return
    void loadReport()
  }, [loadReport, parksLoading])

  return (
    <PageShell
      actions={
        <button
          className="btn btn-secondary"
          disabled={loading || parksLoading || !parks.length}
          onClick={() => void loadReport()}
          type="button"
        >
          {loading ? <Spinner label="Обновление" /> : 'Обновить'}
        </button>
      }
      subtitle="Живой срез открытых blocker по вашим паркам."
      title="Сейчас по Tracker"
    >
      {error && <Alert tone="error">{error}</Alert>}

      <Panel hint="Выберите парк или оставьте «Все парки» для сводки по назначенным." title="Парк">
        {parksLoading ? (
          <SkeletonList rows={1} />
        ) : (
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Охват</span>
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
            </label>
            {report && (
              <p className="field-hint">
                Сформировано: {formatGeneratedAt(report.generated_at)} · охват:{' '}
                {report.scope === 'park' ? 'один парк' : 'все парки'}
              </p>
            )}
          </div>
        )}
        {!parks.length && !error && !parksLoading && (
          <EmptyBlock
            action={
              <Link className="btn btn-secondary" to="/operator/parks">
                Мои парки
              </Link>
            }
            hint="Запросите доступ на странице «Мои парки»."
            icon="🏭"
            title="Нет назначенных парков"
          />
        )}
      </Panel>

      <Panel title="Итого">
        {loading && <SkeletonKpi items={4} />}
        {!loading && report && <MetricsGrid metrics={report.totals} />}
        {!loading && !report && !error && parks.length > 0 && (
          <EmptyBlock hint="Нажмите «Обновить» в шапке." icon="◔" title="Сводка ещё не загружена" />
        )}
      </Panel>

      <Panel title="По паркам">
        {loading && <SkeletonList rows={2} />}
        {!loading && report && !report.parks.length && (
          <EmptyBlock icon="📋" title="Нет парков с метриками для выбранного охвата" />
        )}
        {!loading && report && report.parks.length > 0 && (
          <ul className="park-card-list">
            {report.parks.map((park) => (
              <li className="park-card" key={park.park_id}>
                <div className="park-card-title">
                  {park.park_name}{' '}
                  <span className="badge badge-muted">{park.park_tag}</span>
                </div>
                <MetricsGrid metrics={park.metrics} />
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {report && report.skipped_parks.length > 0 && (
        <Alert tone="warning">
          <strong>Пропущенные парки.</strong>{' '}
          {report.skipped_parks
            .map((park) => `${park.park_name} — ${skipReasonLabel(park.reason)}`)
            .join('; ')}
        </Alert>
      )}
    </PageShell>
  )
}
