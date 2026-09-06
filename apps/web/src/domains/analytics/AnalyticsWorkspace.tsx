import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type Park, type User } from '../../api'
import { useParkScope } from '../../app/park/parkScope'
import { useAuth } from '../../auth-context'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { ANALYTICS_LABELS, analyticsDate, analyticsParks, analyticsSearch, analyticsValue, parseAnalyticsQuery, trendSegments, type AnalyticsApiClient, type AnalyticsCoverage, type AnalyticsMetric, type AnalyticsQuery, type AnalyticsSeries, type HistoricalAnalytics } from './analyticsModel'
import './analytics.css'

function Coverage({ data }: { data: AnalyticsCoverage }) {
  return <span className="rp-analytics-note">Покрытие: {data.observed_buckets} / {data.expected_buckets} интервалов по 2 ч{data.complete ? ' · полное' : ' · неполное'}</span>
}

function TaskKeys({ keys, parkId, label = 'Задачи в наблюдениях' }: { keys: string[]; parkId: number; label?: string }) {
  if (!keys.length) return null
  return <details className="rp-analytics-drilldown"><summary>{label} ({keys.length})</summary><ul>{keys.map(key => <li key={key}><Link aria-label={`Открыть задачу ${key}`} to={`/work/${encodeURIComponent(key)}?park=${parkId}`}>{key}</Link></li>)}</ul></details>
}

function Trend({ data }: { data: AnalyticsSeries }) {
  const segments = trendSegments(data.points)
  if (!segments.length) return <p className="rp-analytics-note">Для графика ещё нет наблюдений.</p>
  return <svg className="rp-analytics-trend" viewBox="0 0 320 104" role="img" aria-label={`${ANALYTICS_LABELS[data.key]}: динамика; пропуски не соединены. Значения доступны в таблице.`}>
    <line x1="8" y1="92" x2="312" y2="92" className="rp-analytics-axis" />
    {segments.map((segment, index) => <g key={index}><polyline points={segment.map(point => `${point.x},${point.y}`).join(' ')} />{segment.map((point, pointIndex) => <circle key={pointIndex} cx={point.x} cy={point.y} r="2.5" />)}</g>)}
  </svg>
}

function SeriesCard({ data }: { data: AnalyticsSeries }) {
  return <article className="rp-analytics-card">
    <h4>{ANALYTICS_LABELS[data.key] ?? data.key}</h4><strong className="rp-analytics-value">{analyticsValue(data)}</strong>
    <Coverage data={data} /><Trend data={data} />
    <details><summary>Значения по интервалам</summary><div className="rp-analytics-table-scroll" tabIndex={0} role="region" aria-label={`Таблица: ${ANALYTICS_LABELS[data.key]}`}><table>
      <caption>{ANALYTICS_LABELS[data.key]} · МСК</caption><thead><tr><th scope="col">Интервал</th><th scope="col">Значение</th><th scope="col">Покрытие</th></tr></thead>
      <tbody>{data.points.map(point => <tr key={point.period.start}><th scope="row">{analyticsDate(point.period.start)} — {analyticsDate(point.period.end)}</th><td>{analyticsValue(point)}</td><td>{point.observed_buckets} / {point.expected_buckets}{point.complete ? '' : ' · неполное'}</td></tr>)}</tbody>
    </table></div></details>
  </article>
}

function MetricRows({ metrics, parkId, durations = false }: { metrics: AnalyticsMetric[]; parkId: number; durations?: boolean }) {
  return <ul className="rp-analytics-rows">{metrics.map(metric => <li key={metric.key}>
    <div><strong>{ANALYTICS_LABELS[metric.key] ?? metric.key}</strong><span>{analyticsValue(metric)}</span></div>
    <Coverage data={metric} />{durations ? <span className="rp-analytics-note">Наблюдаемых переходов: {metric.sample_count}</span> : null}
    <TaskKeys keys={metric.task_keys} parkId={parkId} label="Связанные задачи" />
  </li>)}</ul>
}

function ParkHistory({ data, park }: { data: HistoricalAnalytics; park: Park }) {
  const incomplete = Object.values(data.coverage).some(coverage => !coverage.complete)
  return <section className="rp-analytics-park" aria-label={`История парка ${park.name}`}>
    <header><h2>{park.name}</h2><p className="rp-analytics-note">{analyticsDate(data.period.start)} — {analyticsDate(data.period.end)} · МСК · завершённые интервалы</p></header>
    {incomplete ? <div className="rp-analytics-warning" role="status"><strong>Неполная история</strong><p>Показаны только измеренные значения. Пропуски остаются пустыми; суммы и средние относятся к доступной части периода.</p></div> : null}
    <div className="rp-analytics-coverage"><span>Поток: <Coverage data={data.coverage.flow} /></span><span>Снимки: <Coverage data={data.coverage.observations} /></span></div>
    <section><h3>Динамика процесса</h3><p className="rp-analytics-note">Поступление и выбытие — сумма за доступные интервалы; незавершённые задачи — среднее по снимкам.</p><div className="rp-analytics-trends">{Object.values(data.series).map(series => <SeriesCard key={series.key} data={series} />)}</div></section>
    <section><h3>Возраст незавершённых задач</h3><p className="rp-analytics-note">Среднее число задач в каждой возрастной группе по снимкам периода. Возраст считается с создания задачи.</p><div className="rp-analytics-age">{data.backlog_age_bands.map(band => <article className="rp-analytics-card" key={band.key}><h4>{ANALYTICS_LABELS[band.key]}</h4><strong className="rp-analytics-value">{analyticsValue(band)}</strong><Coverage data={band} /><TaskKeys keys={band.task_keys} parkId={park.id} label="Связанные задачи" /></article>)}</div></section>
    <section><h3>Динамика SLA</h3><p className="rp-analytics-note">Доля просрочек среди наблюдений с известным возрастом и нормативом на момент снимка. Календарные часы, 24/7; одна задача может участвовать в нескольких снимках.</p>
      {data.warnings.includes('sla_policy_or_age_unavailable') ? <p className="rp-analytics-warning">Для части периода нет норматива SLA или возраста задач. Доля без данных недоступна.</p> : null}<SeriesCard data={data.sla_trend} />
    </section>
    <div className="rp-analytics-columns"><section><h3>Наблюдаемая длительность этапов</h3><p className="rp-analytics-note">Среднее время от первого наблюдения статуса до замеченной смены статуса в этом периоде. Требуются минимум два наблюдения и смена статуса. Это оценка по снимкам, точное время перехода неизвестно.</p><MetricRows metrics={data.stage_durations} parkId={park.id} durations /></section>
      <section><h3>Нагрузка по этапам</h3><p className="rp-analytics-note">Среднее число незавершённых задач на этапе по снимкам периода.</p><MetricRows metrics={data.workload} parkId={park.id} /></section></div>
    {data.warnings.includes('flow_unavailable_for_status_scope') ? <p className="rp-analytics-warning">История потока недоступна для вашей области статусов.</p> : null}
    <p className="rp-analytics-note">Исторические счётчики потока не содержат ключей задач. Переходы ниже относятся к задачам, попавшим в снимки; их статус мог измениться.</p>
    <TaskKeys keys={data.drilldown_task_keys} parkId={park.id} />
  </section>
}

function Comparison({ data, parks }: { data: HistoricalAnalytics[]; parks: Park[] }) {
  if (data.length < 2) return null
  return <section className="rp-analytics-comparison"><h2>Сравнение парков</h2>
    <p className="rp-analytics-note">Одинаковый период и шаг. При разном покрытии сравнение сумм ограничено доступными наблюдениями.</p>
    <div className="rp-analytics-table-scroll" role="region" tabIndex={0} aria-label="Показатели парков"><table aria-label="Сравнение парков">
      <thead><tr><th scope="col">Показатель</th>{parks.map(park => <th scope="col" key={park.id}>{park.name}</th>)}</tr></thead>
      <tbody>{['arrived', 'departed', 'backlog', 'overdue_share'].map(key => <tr key={key}><th scope="row">{ANALYTICS_LABELS[key]}</th>{data.map(park => {
        const metric = key === 'overdue_share' ? park.sla_trend : park.series[key]
        return <td key={park.park_id}><strong>{analyticsValue(metric)}</strong><Coverage data={metric} /></td>
      })}</tr>)}</tbody>
    </table></div>
  </section>
}

function AnalyticsOwner({ apiClient, parks, days, bucket, onAuthorizationFailure }: {
  apiClient: AnalyticsApiClient; parks: Park[]; days: number; bucket: AnalyticsQuery['bucket']; onAuthorizationFailure: (failure: DomainError) => void
}) {
  const [data, setData] = useState<HistoricalAnalytics[] | null>(null)
  const [failure, setFailure] = useState<DomainError | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setData(null); setFailure(null)
    void Promise.all(parks.map(async park => {
      const result = await apiClient.analytics(park.id, days, bucket)
      if (result.park_id !== park.id) throw new Error('analytics_park_mismatch')
      return result
    })).then(results => { if (active) setData(results) }, error => {
      if (!active) return
      const next = classifyApiError(error, 'Не удалось загрузить историю процесса.')
      setFailure(next)
      if (next.kind === 'forbidden' || next.kind === 'unauthorized') onAuthorizationFailure(next)
    })
    return () => { active = false }
  }, [apiClient, parks, days, bucket, attempt, onAuthorizationFailure])
  if (failure) return <ErrorState title={failure.title} description={failure.description} requestId={failure.requestId} onRetry={failure.retryable ? () => setAttempt(value => value + 1) : undefined} />
  if (!data) return <LoadingState label="Загружаем историю процесса" variant="page" />
  return <><Comparison data={data} parks={parks} /><div className="rp-analytics-parks">{data.map((result, index) => <ParkHistory key={result.park_id} data={result} park={parks[index]} />)}</div><Button variant="secondary" leadingIcon="refresh" onClick={() => setAttempt(value => value + 1)}>Обновить историю</Button></>
}

function AnalyticsSession({ apiClient, user }: { apiClient: AnalyticsApiClient; user: User }) {
  const { refreshUser } = useAuth()
  const { selectedPark, parks, loading } = useParkScope()
  const [params, setParams] = useSearchParams()
  const available = useMemo(() => analyticsParks(user, parks), [user, parks])
  const query = parseAnalyticsQuery(params, available, selectedPark?.id)
  const normalized = analyticsSearch(params, query).toString()
  const [authorizationFailure, setAuthorizationFailure] = useState<DomainError | null>(null)
  const onAuthorizationFailure = useCallback((failure: DomainError) => {
    setAuthorizationFailure(failure)
    void refreshUser().catch(() => undefined)
  }, [refreshUser])
  useLayoutEffect(() => {
    if (!loading && normalized !== params.toString()) setParams(normalized, { replace: true })
  }, [loading, normalized, params, setParams])
  const comparePark = available.find(park => park.id === query.compare)
  const requestedParks = useMemo(() => selectedPark ? [selectedPark, ...(comparePark ? [comparePark] : [])] : [], [selectedPark, comparePark])
  const identity = JSON.stringify([user.id, user.role, user.access_status, user.permissions, user.parks, requestedParks, query.days, query.bucket])
  const update = (next: AnalyticsQuery) => setParams(analyticsSearch(params, next), { replace: true })
  const canRead = user.access_status === 'approved' && !user.must_change_password && ['nav.analytics', 'tracker.read'].every(permission => user.permissions?.includes(permission))
  return <PageLayout title="Аналитика" description="Как меняется процесс и на каких этапах накапливается задержка.">
    {canRead ? <div className="rp-analytics-controls">
      <label>Период аналитики<select aria-label="Период аналитики" value={query.days} onChange={event => update({ ...query, days: Number(event.target.value) })}><option value="1">1 день</option><option value="7">7 дней</option><option value="30">30 дней</option></select></label>
      <label>Шаг графиков<select aria-label="Шаг графиков" value={query.bucket} onChange={event => update({ ...query, bucket: event.target.value as AnalyticsQuery['bucket'] })}><option value="1d">24 часа</option><option value="2h">2 часа</option></select></label>
      <label>Сравнить с парком<select aria-label="Сравнить с парком" value={query.compare ?? ''} onChange={event => update({ ...query, compare: event.target.value ? Number(event.target.value) : null })}><option value="">Без сравнения</option>{available.filter(park => park.id !== selectedPark?.id).map(park => <option key={park.id} value={park.id}>{park.name}</option>)}</select></label>
    </div> : null}
    {!canRead ? <ErrorState title="Нет доступа" description="Нужны разрешения на аналитику и чтение Tracker." /> : authorizationFailure ? <ErrorState title={authorizationFailure.title} description={authorizationFailure.description} />
      : loading ? <LoadingState label="Загружаем доступные парки" /> : !selectedPark ? <EmptyState title="Парк не выбран" description="Выберите парк для просмотра истории процесса." icon="parks" />
        : !available.some(park => park.id === selectedPark.id) ? <ErrorState title="Нет доступа" description="Выбранный парк недоступен." />
          : <AnalyticsOwner key={identity} apiClient={apiClient} parks={requestedParks} days={query.days} bucket={query.bucket} onAuthorizationFailure={onAuthorizationFailure} />}
  </PageLayout>
}

export function AnalyticsWorkspace({ apiClient = api }: { apiClient?: AnalyticsApiClient }) {
  const { user } = useAuth()
  return user ? <AnalyticsSession key={user.id} apiClient={apiClient} user={user} /> : null
}
