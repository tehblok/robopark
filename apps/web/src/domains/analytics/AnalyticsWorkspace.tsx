import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type Park, type User } from '../../api'
import { useParkScope } from '../../app/park/parkScope'
import { useAuth } from '../../auth-context'
import { Button } from '../../design-system/actions/Button'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout } from '../../design-system/layout/PageLayout'
import { classifyApiError, type DomainError } from '../../shared/api/classifyApiError'
import { ANALYTICS_LABELS, analyticsDate, analyticsParks, analyticsRequestIdentity, analyticsSearch, analyticsValue, buildOperationalInsights, parseAnalyticsQuery, trendSegments, type AnalyticsApiClient, type AnalyticsCoverage, type AnalyticsMetric, type AnalyticsQuery, type AnalyticsSeries, type HistoricalAnalytics } from './analyticsModel'
import { limitOperationsRequest } from '../shift/operationsRequestLimit'
import './analytics.css'
import { useInterfaceMode } from '../../app/interface/InterfaceModeProvider'

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
  const { mode } = useInterfaceMode()
  const incomplete = Object.values(data.coverage).some(coverage => !coverage.complete)
  return <section className="rp-analytics-park" aria-label={`История парка ${park.name}`}>
    <header><h2>{park.name}</h2><p className="rp-analytics-note">{analyticsDate(data.period.start)} — {analyticsDate(data.period.end)} · МСК · завершённые интервалы</p></header>
    {incomplete ? <div className="rp-analytics-warning" role="status"><strong>Неполная история</strong><p>Показаны только измеренные значения. Пропуски остаются пустыми; суммы и средние относятся к доступной части периода.</p></div> : null}
    <div className="rp-analytics-coverage"><span>Поток: <Coverage data={data.coverage.flow} /></span><span>Снимки: <Coverage data={data.coverage.observations} /></span></div>
    <details className="a-analytics-section" open={mode === 'classic' ? true : undefined}><summary><h3>Динамика процесса</h3></summary><p className="rp-analytics-note">Поступление и выбытие — сумма за доступные интервалы; незавершённые задачи — среднее по снимкам.</p><div className="rp-analytics-trends">{Object.values(data.series).map(series => <SeriesCard key={series.key} data={series} />)}</div></details>
    <details className="a-analytics-section" open={mode === 'classic' ? true : undefined}><summary><h3>Возраст незавершённых задач</h3></summary><p className="rp-analytics-note">Среднее число задач в каждой возрастной группе по снимкам периода. Возраст считается с создания задачи.</p><div className="rp-analytics-age">{data.backlog_age_bands.map(band => <article className="rp-analytics-card" key={band.key}><h4>{ANALYTICS_LABELS[band.key]}</h4><strong className="rp-analytics-value">{analyticsValue(band)}</strong><Coverage data={band} /><TaskKeys keys={band.task_keys} parkId={park.id} label="Связанные задачи" /></article>)}</div></details>
    <details className="a-analytics-section" open={mode === 'classic' ? true : undefined}><summary><h3>Динамика SLA</h3></summary><p className="rp-analytics-note">Доля просрочек среди наблюдений с известным возрастом и нормативом на момент снимка. Учитываются рабочие часы 09:00–21:00 МСК; одна задача может участвовать в нескольких снимках.</p>
      {data.warnings.includes('sla_policy_or_age_unavailable') ? <p className="rp-analytics-warning">Для части периода нет норматива SLA или возраста задач. Доля без данных недоступна.</p> : null}<SeriesCard data={data.sla_trend} />
    </details>
    <details className="a-analytics-details" open={mode === 'classic' ? true : undefined}><summary>Этапы работы и связанные задачи</summary>
    <div className="rp-analytics-columns"><section><h3>Наблюдаемая длительность этапов</h3><p className="rp-analytics-note">Среднее время от первого наблюдения статуса до замеченной смены статуса в этом периоде. Требуются минимум два наблюдения и смена статуса. Это оценка по снимкам, точное время перехода неизвестно.</p><MetricRows metrics={data.stage_durations} parkId={park.id} durations /></section>
      <section><h3>Нагрузка по этапам</h3><p className="rp-analytics-note">Среднее число незавершённых задач на этапе по снимкам периода.</p><MetricRows metrics={data.workload} parkId={park.id} /></section></div>
    {data.warnings.includes('flow_unavailable_for_status_scope') ? <p className="rp-analytics-warning">История потока недоступна для вашей области статусов.</p> : null}
    <p className="rp-analytics-note">Исторические счётчики потока не содержат ключей задач. Переходы ниже относятся к задачам, попавшим в снимки; их статус мог измениться.</p>
    <TaskKeys keys={data.drilldown_task_keys} parkId={park.id} />
    </details>
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

function OperationalAnalysis({ data, parks }: { data: HistoricalAnalytics[]; parks: Park[] }) {
  const insights = buildOperationalInsights(data, parks)
  return <section className="rp-analytics-analysis" aria-labelledby="operational-analysis-title">
    <h2 id="operational-analysis-title">Анализ текущей ситуации</h2>
    <p className="rp-analytics-note">Автоматическая оценка истории очереди, возраста задач, SLA и нагрузки по этапам.</p>
    {insights.length ? <ul>{insights.map((item, index) => <li className={`rp-analytics-insight rp-analytics-insight--${item.severity}`} key={`${item.parkId}-${index}`}>{item.text}</li>)}</ul> : <p>Критичных отклонений по доступным данным не обнаружено.</p>}
  </section>
}

function AnalyticsOwner({ apiClient, parks, days, bucket, resourceKey, onAuthorizationFailure, showAnalysis }: {
  apiClient: AnalyticsApiClient; parks: Park[]; days: number; bucket: AnalyticsQuery['bucket']; resourceKey: string; onAuthorizationFailure: (failure: DomainError) => void; showAnalysis: boolean
}) {
  const activeLoads = useRef(0)
  const ownerGeneration = useRef(Symbol('analytics-owner'))
  useLayoutEffect(() => {
    ownerGeneration.current = Symbol('analytics-owner')
    return () => { ownerGeneration.current = Symbol('retired-analytics-owner') }
  }, [])
  const resource = useCachedResource(resourceKey, async () => {
    activeLoads.current += 1
    const generation = ownerGeneration.current
    const results: HistoricalAnalytics[] = new Array(parks.length)
    let next = 0
    let stopped = false
    const active = () => !stopped && generation === ownerGeneration.current
    // Large accessible scopes share three workers; retired scopes never launch queued calls.
    const worker = async () => {
      while (active() && next < parks.length) {
        const index = next++
        const park = parks[index]
        try {
          const result = await limitOperationsRequest(async () => {
            if (!active()) throw new Error('analytics_owner_retired')
            try {
              const result = await apiClient.analytics(park.id, days, bucket)
              if (result.park_id !== park.id) throw new Error('analytics_park_mismatch')
              return result
            } catch (error) {
              // Observe each current request's denial, even after a sibling failed.
              const failure = classifyApiError(error, 'Не удалось загрузить историю процесса.')
              if (generation === ownerGeneration.current && (failure.kind === 'unauthorized' || failure.kind === 'forbidden')) onAuthorizationFailure(failure)
              // Stop this scope before releasing the shared request slot.
              stopped = true
              throw error
            }
          })
          if (!active()) return
          results[index] = result
        } catch (error) {
          stopped = true
          throw error
        }
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(3, parks.length) }, worker))
      if (!active()) throw new Error('analytics_owner_retired')
      return results
    } finally { activeLoads.current -= 1 }
  }, { persist: false, refreshIntervalMs: 120_000, staleTimeMs: 120_000 })
  // Retain settled history, but do not lend a retired owner's request to a remount.
  useLayoutEffect(() => () => { if (activeLoads.current > 0) resourceStore.cancelPending(resourceKey) }, [resourceKey])
  const failure = useMemo(() => resource.error ? classifyApiError(resource.error, 'Не удалось загрузить историю процесса.') : null, [resource.error])
  useEffect(() => {
    if (failure?.kind === 'forbidden' || failure?.kind === 'unauthorized') onAuthorizationFailure(failure)
  }, [failure, onAuthorizationFailure])
  const data = resource.data
  // An authorization failure must never paint protected cached history.
  if (failure?.kind === 'forbidden' || failure?.kind === 'unauthorized') return null
  const retainData = failure && ['offline', 'timeout', 'server'].includes(failure.kind)
  if (failure && !(data && retainData)) return <ErrorState title={failure.title} description={failure.description} requestId={failure.requestId} onRetry={failure.retryable ? () => void resource.refresh() : undefined} />
  if (!data) return <LoadingState label="Загружаем историю процесса" variant="page" />
  return <>{failure ? <div className="rp-analytics-warning" role="alert"><strong>{failure.title}</strong><p>{failure.description}</p>{failure.retryable ? <Button variant="secondary" busy={resource.isRevalidating} onClick={() => void resource.refresh()}>Повторить</Button> : null}</div> : null}{showAnalysis ? <OperationalAnalysis data={data} parks={parks} /> : null}<Comparison data={data} parks={parks} /><div className="rp-analytics-parks">{data.map((result, index) => <ParkHistory key={result.park_id} data={result} park={parks[index]} />)}</div></>
}

function AnalyticsSession({ apiClient, user }: { apiClient: AnalyticsApiClient; user: User }) {
  const { refreshUser } = useAuth()
  const { selectedPark, parks, loading, allowAllParks } = useParkScope()
  const [params, setParams] = useSearchParams()
  const available = useMemo(() => analyticsParks(user, parks), [user, parks])
  const allParks = Boolean(allowAllParks && !selectedPark)
  const parsedQuery = parseAnalyticsQuery(params, available, selectedPark?.id)
  const query = allParks ? { ...parsedQuery, compare: null } : parsedQuery
  const normalized = analyticsSearch(params, query).toString()
  const [authorizationFailure, setAuthorizationFailure] = useState<{ context: string; failure: DomainError } | null>(null)
  useEffect(() => {
    if (!loading && normalized !== params.toString()) setParams(normalized, { replace: true })
  }, [loading, normalized, params, setParams])
  const comparePark = available.find(park => park.id === query.compare)
  const requestedParks = useMemo(() => allParks ? available : selectedPark ? [selectedPark, ...(comparePark ? [comparePark] : [])] : [], [allParks, available, selectedPark, comparePark])
  const identity = analyticsRequestIdentity(user, requestedParks, query)
  const refreshStarted = useRef(new Set<string>())
  const contextFailure = authorizationFailure?.failure.kind === 'unauthorized' || authorizationFailure?.context === identity ? authorizationFailure?.failure : null
  const onAuthorizationFailure = useCallback((failure: DomainError) => {
    setAuthorizationFailure({ context: identity, failure })
    const refreshKey = failure.kind === 'unauthorized' ? 'session' : identity
    if (!refreshStarted.current.has(refreshKey)) {
      refreshStarted.current.add(refreshKey)
      void refreshUser().catch(() => undefined)
    }
  }, [identity, refreshUser, setAuthorizationFailure])
  useEffect(() => {
    if (authorizationFailure) resourceStore.invalidate(`analytics:${user.id}:`, { prefix: true })
  }, [authorizationFailure, user.id])
  const update = (next: AnalyticsQuery) => setParams(analyticsSearch(params, next), { replace: true })
  const canRead = user.access_status === 'approved' && !user.must_change_password && ['nav.analytics', 'tracker.read'].every(permission => user.permissions?.includes(permission))
  return <PageLayout title="Аналитика" description="Как меняется процесс и на каких этапах накапливается задержка.">
    {canRead ? <div aria-label="Параметры аналитики" className="rp-analytics-controls rp-analytics-controls--single-row" role="group">
      <label>Период аналитики<select aria-label="Период аналитики" value={query.days} onChange={event => update({ ...query, days: Number(event.target.value) })}><option value="1">1 день</option><option value="7">7 дней</option><option value="30">30 дней</option></select></label>
      <label>Шаг графиков<select aria-label="Шаг графиков" value={query.bucket} onChange={event => update({ ...query, bucket: event.target.value as AnalyticsQuery['bucket'] })}><option value="1d">24 часа</option><option value="2h">2 часа</option></select></label>
      {!allParks ? <label>Сравнить с парком<select aria-label="Сравнить с парком" value={query.compare ?? ''} onChange={event => update({ ...query, compare: event.target.value ? Number(event.target.value) : null })}><option value="">Без сравнения</option>{available.filter(park => park.id !== selectedPark?.id).map(park => <option key={park.id} value={park.id}>{park.name}</option>)}</select></label> : null}
    </div> : null}
    {allParks && canRead ? <h2>Все доступные парки</h2> : null}
    {!canRead ? <ErrorState title="Нет доступа" description="Нужны разрешения на аналитику и чтение Tracker." /> : contextFailure ? <ErrorState title={contextFailure.title} description={contextFailure.description} />
      : loading ? <LoadingState label="Загружаем доступные парки" /> : allParks && !available.length ? <EmptyState title="Нет доступных парков" description="История появится после назначения доступа к активному парку." icon="parks" /> : !allParks && !selectedPark ? <EmptyState title="Парк не выбран" description="Выберите парк для просмотра истории процесса." icon="parks" />
        : selectedPark && !available.some(park => park.id === selectedPark.id) ? <ErrorState title="Нет доступа" description="Выбранный парк недоступен." />
          : <AnalyticsOwner key={identity} resourceKey={`analytics:${user.id}:${identity}`} apiClient={apiClient} parks={requestedParks} days={query.days} bucket={query.bucket} onAuthorizationFailure={onAuthorizationFailure} showAnalysis={user.role === 'admin' || user.role === 'royal'} />}
  </PageLayout>
}

export function AnalyticsWorkspace({ apiClient = api }: { apiClient?: AnalyticsApiClient }) {
  const { user } = useAuth()
  return user ? <AnalyticsSession key={user.id} apiClient={apiClient} user={user} /> : null
}
