import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type NowReport } from '../api'
import { Alert, PageShell, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonKpi, SkeletonList, Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'
import { useParkContext } from '../park-context'

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
  const { parkId, setParkId, parks, parksLoading } = useParkContext()
  const [scopeAll, setScopeAll] = useState(false)

  const reportEnabled = parks.length > 0 && (scopeAll || parkId != null)
  const reportKey = !reportEnabled
    ? ''
    : scopeAll
      ? 'now-report:all'
      : `now-report:park:${parkId}`
  const reportRes = useCachedResource<NowReport>(
    reportKey,
    () => api.operatorNowReport(scopeAll ? undefined : parkId ?? undefined),
    { enabled: reportEnabled },
  )

  const report = reportRes.data
  const showReportSkeleton = reportEnabled && reportRes.isLoading && !report
  const error = reportRes.error ? mapApiError(reportRes.error, ru.errors.load) : ''

  return (
    <PageShell
      actions={
        <button
          className="btn btn-secondary"
          disabled={reportRes.isRevalidating || parksLoading || !reportEnabled}
          onClick={() => void reportRes.refresh()}
          type="button"
        >
          {reportRes.isRevalidating ? <Spinner label="Обновление" /> : 'Обновить'}
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
                  if (!value) {
                    setScopeAll(true)
                    return
                  }
                  setParkId(Number(value))
                  setScopeAll(false)
                }}
                value={scopeAll ? '' : (parkId ?? '')}
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
        {showReportSkeleton && <SkeletonKpi items={4} />}
        {report && <MetricsGrid metrics={report.totals} />}
        {!showReportSkeleton && !report && !error && reportEnabled && (
          <EmptyBlock hint="Нажмите «Обновить» в шапке." icon="◔" title="Сводка ещё не загружена" />
        )}
      </Panel>

      <Panel title="По паркам">
        {showReportSkeleton && <SkeletonList rows={2} />}
        {report && !report.parks.length && (
          <EmptyBlock icon="📋" title="Нет парков с метриками для выбранного охвата" />
        )}
        {report && report.parks.length > 0 && (
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
