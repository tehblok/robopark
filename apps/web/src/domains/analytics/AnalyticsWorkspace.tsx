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
import { ANALYTICS_LABELS, analyticsDate, analyticsParks, analyticsRequestIdentity, analyticsSearch, analyticsValue, buildOperationalInsights, parseAnalyticsQuery, trendSegments, type AnalyticsApiClient, type AnalyticsCoverage, type AnalyticsMetric, type AnalyticsQuery, type AnalyticsSeries, type AnalyticsTaskKeysGroup, type HistoricalAnalytics, type VerifiedClosureSummary } from './analyticsModel'
import { limitOperationsRequest } from '../shift/operationsRequestLimit'
import { formatDurationHours } from '../../lib/timeFormat'
import './analytics.css'

function Coverage({ data }: { data: AnalyticsCoverage }) {
  return <span className="rp-analytics-note">Покрытие: {data.observed_buckets} / {data.expected_buckets} интервалов по 2 ч{data.complete ? ' · полное' : ' · неполное'}</span>
}

type DrilldownContext = { apiClient: AnalyticsApiClient; days: number; bucket: AnalyticsQuery['bucket']; periodEnd: string; generatedAt: string; onAuthorizationFailure: (failure: DomainError) => void }

function TaskKeys({ keys, total, parkId, label = 'Задачи в наблюдениях', group, metricKey, context }: {
  keys: string[]; total?: number; parkId: number; label?: string; group: AnalyticsTaskKeysGroup; metricKey?: string; context: DrilldownContext
}) {
  const [loaded, setLoaded] = useState(keys)
  const [offset, setOffset] = useState(keys.length)
  const [knownTotal, setKnownTotal] = useState(total ?? keys.length)
  const [hasMore, setHasMore] = useState((total ?? keys.length) > keys.length)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generation = useRef(0)
  const keySignature = JSON.stringify(keys)
  useEffect(() => {
    const initialKeys = JSON.parse(keySignature) as string[]
    generation.current += 1
    setLoaded(initialKeys)
    setOffset(initialKeys.length)
    setKnownTotal(total ?? initialKeys.length)
    setHasMore((total ?? initialKeys.length) > initialKeys.length)
    setBusy(false)
    setError(null)
    return () => { generation.current += 1 }
  }, [keySignature, total, context.periodEnd, context.generatedAt, group, metricKey])
  if (!knownTotal) return null
  const loadMore = async () => {
    if (busy || !context.apiClient.analyticsTaskKeys) return
    const current = generation.current
    setBusy(true)
    setError(null)
    try {
      const page = await context.apiClient.analyticsTaskKeys(parkId, context.days, context.bucket, context.periodEnd, group, metricKey, offset, loaded.at(-1))
      if (generation.current !== current) return
      setLoaded(previous => [...new Set([...previous, ...page.task_keys])])
      setOffset(offset + page.task_keys.length)
      setKnownTotal(page.total)
      setHasMore(page.has_more)
    } catch (reason) {
      if (generation.current !== current) return
      const failure = classifyApiError(reason, 'Не удалось загрузить задачи.')
      if (failure.kind === 'unauthorized' || failure.kind === 'forbidden') context.onAuthorizationFailure(failure)
      else setError(failure.description)
    } finally {
      if (generation.current === current) setBusy(false)
    }
  }
  return <details className="rp-analytics-drilldown"><summary>{label} ({knownTotal})</summary><ul>{loaded.map(key => <li key={key}><Link aria-label={`Открыть задачу ${key}`} to={`/work/${encodeURIComponent(key)}?park=${parkId}`}>{key}</Link></li>)}</ul>
    {error ? <p role="alert">{error}</p> : null}
    {hasMore && context.apiClient.analyticsTaskKeys ? <Button variant="secondary" busy={busy} onClick={() => void loadMore()}>Показать ещё задачи</Button> : null}
  </details>
}

function Trend({ data }: { data: AnalyticsSeries }) {
  const segments = trendSegments(data.points)
  if (!segments.length) return <p className="rp-analytics-note">Для графика ещё нет наблюдений.</p>
  if (segments.every(segment => segment.length < 2)) return <p className="rp-analytics-note">Для графика нужны хотя бы два соседних наблюдения. Доступные значения остаются в таблице.</p>
  return <svg className="rp-analytics-trend" viewBox="0 0 320 104" role="img" aria-label={`${ANALYTICS_LABELS[data.key]}: динамика; пропуски не соединены. Значения доступны в таблице.`}>
    <line x1="8" y1="92" x2="312" y2="92" className="rp-analytics-axis" />
    {segments.map((segment, index) => <g key={index}><polyline points={segment.map(point => `${point.x},${point.y}`).join(' ')} />{segment.map((point, pointIndex) => <circle key={pointIndex} cx={point.x} cy={point.y} r="2.5" />)}</g>)}
  </svg>
}

function SeriesCard({ data, timezone, parkId, context, group }: { data: AnalyticsSeries; timezone: string; parkId: number; context: DrilldownContext; group: 'series' | 'sla' }) {
  return <article className="rp-analytics-card">
    <h4>{ANALYTICS_LABELS[data.key] ?? data.key}</h4><strong className="rp-analytics-value">{analyticsValue(data)}</strong>
    <Coverage data={data} /><Trend data={data} />
    <details><summary>Значения по интервалам</summary><div className="rp-analytics-table-scroll" tabIndex={0} role="region" aria-label={`Таблица: ${ANALYTICS_LABELS[data.key]}`}><table>
      <caption>{ANALYTICS_LABELS[data.key]} · {timezone}</caption><thead><tr><th scope="col">Интервал</th><th scope="col">Значение</th><th scope="col">Покрытие</th></tr></thead>
      <tbody>{data.points.map(point => <tr key={point.period.start}><th scope="row">{analyticsDate(point.period.start, timezone)} — {analyticsDate(point.period.end, timezone)}</th><td>{analyticsValue(point)}</td><td>{point.observed_buckets} / {point.expected_buckets}{point.complete ? '' : ' · неполное'}</td></tr>)}</tbody>
    </table></div></details>
    <TaskKeys keys={data.task_keys} total={data.task_keys_count} parkId={parkId} label="Связанные задачи" group={group} metricKey={data.key} context={context} />
  </article>
}

function MetricRows({ metrics, parkId, context, group, durations = false }: { metrics: AnalyticsMetric[]; parkId: number; context: DrilldownContext; group: AnalyticsTaskKeysGroup; durations?: boolean }) {
  return <ul className="rp-analytics-rows">{metrics.map(metric => <li key={metric.key}>
    <div><strong>{ANALYTICS_LABELS[metric.key] ?? metric.key}</strong><span>{analyticsValue(metric)}</span></div>
    <Coverage data={metric} />{durations ? <span className="rp-analytics-note">Наблюдаемых переходов: {metric.sample_count}</span> : null}
    <TaskKeys keys={metric.task_keys} total={metric.task_keys_count} parkId={parkId} label="Связанные задачи" group={group} metricKey={metric.key} context={context} />
  </li>)}</ul>
}

function VerifiedClosureCard({ summary, parkId, context }: { summary: VerifiedClosureSummary; parkId: number; context: DrilldownContext }) {
  const evaluated = summary.sla_on_time_count !== null && summary.sla_late_count !== null
    ? summary.sla_on_time_count + summary.sla_late_count
    : null
  const countLabel = summary.count === null
    ? 'Недоступно для этой роли'
    : summary.count === 0
      ? 'В доступной истории закрытия не подтверждены'
      : `Не менее ${summary.count} ${summary.count % 10 === 1 && summary.count % 100 !== 11 ? 'закрытой задачи' : 'закрытых задач'}`
  return <section aria-label="Подтверждённые закрытия" className="rp-analytics-card rp-analytics-closures">
    <h3>Подтверждённые закрытия</h3>
    <strong className="rp-analytics-value">{countLabel}</strong>
    {summary.count !== null && summary.count > 0 ? <p className="rp-analytics-note">
      В пределах SLA: {summary.sla_on_time_percent === null
        ? 'неизвестно'
        : `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(summary.sla_on_time_percent)}%`}
      {evaluated !== null ? ` · ${summary.sla_on_time_count} из ${evaluated} подтверждённых задач` : null}
      {summary.sla_unknown_count ? ` · без расчёта SLA: ${summary.sla_unknown_count}` : null}
    </p> : null}
    <details className="rp-analytics-method"><summary>Как считаем закрытия и простой</summary>
    {summary.count !== null && summary.count > 0 ? <>
      <div className="rp-analytics-closure-metrics">
        <div><span className="rp-analytics-note">Медиана простоя</span><strong>{summary.median_downtime_hours === null ? 'Неизвестно' : formatDurationHours(summary.median_downtime_hours)}</strong></div>
        <div><span className="rp-analytics-note">90-й процентиль</span><strong>{summary.p90_downtime_hours === null ? 'Неизвестно' : formatDurationHours(summary.p90_downtime_hours)}</strong></div>
      </div>
    </> : null}
      {summary.count !== null && summary.count > 0 ? <p className="rp-analytics-note">Измеренных закрытий: {summary.downtime_sample_count}. Календарное время от первого входа в очередь до последнего закрытия, включая время между закрытием и повторным открытием. 90-й процентиль — ближайшее ранговое значение.</p> : null}
      <p className="rp-analytics-note">Учтены задачи, чей последний подтверждённый статус — «Закрыт» или «Решён» и последний переход пришёлся на период. Повторное закрытие одной задачи считается один раз; снова открытая задача возвращается в активный хвост. Парк определяется по подтверждённому переходу в закрытый статус; SLA сохраняет время и часовой пояс первого входа в очередь. История догружается постепенно, поэтому число может быть ниже фактического; отменённые задачи не включены.</p>
    </details>
    <TaskKeys keys={summary.task_keys} total={summary.count ?? 0} parkId={parkId} label="Связанные задачи" group="closures" context={context} />
  </section>
}

function ParkHistory({ data, park, context }: { data: HistoricalAnalytics; park: Park; context: DrilldownContext }) {
  const incomplete = Object.values(data.coverage).some(coverage => !coverage.complete)
  return <section className="rp-analytics-park" aria-label={`История парка ${park.name}`}>
    <header><h2>{park.name}</h2><p className="rp-analytics-note">{analyticsDate(data.period.start, data.timezone)} — {analyticsDate(data.period.end, data.timezone)} · {data.timezone} · завершённые интервалы</p></header>
    {incomplete ? <div className="rp-analytics-warning" role="status"><strong>Неполная история</strong><p>Показаны только измеренные значения. Пропуски остаются пустыми; суммы и средние относятся к доступной части периода.</p></div> : null}
    <div className="rp-analytics-coverage"><span>Поток: <Coverage data={data.coverage.flow} /></span><span>Снимки: <Coverage data={data.coverage.observations} /></span></div>
    <VerifiedClosureCard summary={data.verified_closures} parkId={park.id} context={context} />
    {data.warnings.includes('closed_history_search_failed') ? <p className="rp-analytics-warning" role="status">Страницу поиска закрытых задач в Tracker не удалось загрузить или обработать. Известные закрытия сохранены; догрузка повторится в следующем цикле.</p> : null}
    {data.warnings.includes('closed_history_page_cap') ? <p className="rp-analytics-warning" role="status">Достигнут предел страниц поиска Tracker. История закрытий может быть неполной; нужен пересмотр диапазона или способа выгрузки.</p> : null}
    <details className="rp-analytics-section" open><summary><h3>Динамика SLA</h3></summary><p className="rp-analytics-note">Доля просрочек среди наблюдений с подтверждённым первым входом в очередь. SLA — 5 рабочих часов, ежедневно с 09:00 до 21:00 по часовому поясу первого входа задачи в очередь; одна задача может участвовать в нескольких снимках.</p>
      {data.warnings.includes('queue_history_unavailable') ? <p className="rp-analytics-warning">Для части задач не удалось рассчитать SLA. Они не включены в долю; покрытие показано рядом с графиком.</p> : null}
      {data.warnings.includes('invalid_history_timezone') ? <p className="rp-analytics-warning">Некорректный часовой пояс в истории задачи. Время простоя остаётся измеренным, но срок SLA неизвестен до исправления данных.</p> : null}
      {data.warnings.includes('history_access_denied') ? <p className="rp-analytics-warning">Tracker отказал в доступе к истории статусов. Проверьте права подключения.</p> : null}
      {data.warnings.includes('history_source_unavailable') ? <p className="rp-analytics-warning">История статусов временно недоступна в Tracker. Система повторит чтение.</p> : null}
      <SeriesCard data={data.sla_trend} timezone={data.timezone} parkId={park.id} context={context} group="sla" />
    </details>
    <details className="rp-analytics-section" open><summary><h3>Динамика процесса</h3></summary><p className="rp-analytics-note">Поступление и выбытие — сумма за доступные интервалы; незавершённые задачи — среднее по снимкам.</p><div className="rp-analytics-trends">{Object.values(data.series).map(series => <SeriesCard key={series.key} data={series} timezone={data.timezone} parkId={park.id} context={context} group="series" />)}</div></details>
    <details className="rp-analytics-section"><summary><h3>Простой незавершённых задач</h3></summary><p className="rp-analytics-note">Среднее число задач в каждой группе по снимкам периода. Простой считается непрерывно от первого входа в очередь, включая время после повторного открытия. Задачи без подтверждённой истории показаны отдельно.</p><div className="rp-analytics-age">{data.backlog_age_bands.map(band => <article className="rp-analytics-card" key={band.key}><h4>{ANALYTICS_LABELS[band.key]}</h4><strong className="rp-analytics-value">{analyticsValue(band)}</strong><Coverage data={band} /><TaskKeys keys={band.task_keys} total={band.task_keys_count} parkId={park.id} label="Связанные задачи" group="age" metricKey={band.key} context={context} /></article>)}</div></details>
    <details className="rp-analytics-details"><summary>Этапы работы и связанные задачи</summary>
    <div className="rp-analytics-columns"><section><h3>Наблюдаемая длительность этапов</h3><p className="rp-analytics-note">Среднее время от первого наблюдения статуса до замеченной смены статуса в этом периоде. Требуются минимум два наблюдения и смена статуса. Это оценка по снимкам, точное время перехода неизвестно.</p><MetricRows metrics={data.stage_durations} parkId={park.id} group="stage" context={context} durations /></section>
      <section><h3>Нагрузка по этапам</h3><p className="rp-analytics-note">Среднее число незавершённых задач на этапе по снимкам периода.</p><MetricRows metrics={data.workload} parkId={park.id} group="workload" context={context} /></section></div>
    {data.warnings.includes('flow_unavailable_for_status_scope') ? <p className="rp-analytics-warning">История потока недоступна для вашей области статусов.</p> : null}
    <p className="rp-analytics-note">Исторические счётчики потока не содержат ключей задач. Переходы ниже относятся к задачам, попавшим в снимки; их статус мог измениться.</p>
    <TaskKeys keys={data.drilldown_task_keys} total={data.drilldown_task_keys_count} parkId={park.id} group="all" context={context} />
    </details>
  </section>
}

function Comparison({ data, parks }: { data: HistoricalAnalytics[]; parks: Park[] }) {
  if (data.length < 2) return null
  const metrics = ['arrived', 'departed', 'backlog', 'overdue_share'] as const
  return <section className="rp-analytics-comparison"><h2>Сравнение парков</h2>
    <p className="rp-analytics-note">Разное покрытие ограничивает сравнение. Закрытия — нижняя граница подтверждённых задач.</p>
    <div className="rp-analytics-table-scroll" role="region" tabIndex={0} aria-label="Показатели парков"><table aria-label="Сравнение парков">
      <thead><tr><th scope="col">Показатель</th>{parks.map(park => <th scope="col" key={park.id}>{park.name}</th>)}</tr></thead>
      <tbody>{metrics.map(key => <tr key={key}><th scope="row">{ANALYTICS_LABELS[key]}</th>{data.map(park => {
        const metric = key === 'overdue_share' ? park.sla_trend : park.series[key]
        return <td key={park.park_id}><strong>{analyticsValue(metric)}</strong><Coverage data={metric} /></td>
      })}</tr>)}<tr><th scope="row">Подтверждённые закрытия</th>{data.map(park => <td key={park.park_id}>
        <strong>{park.verified_closures.count === null ? 'Недоступно' : park.verified_closures.count === 0 ? 'Нет подтверждения' : `≥ ${park.verified_closures.count}`}</strong>
        <span className="rp-analytics-note">Нижняя граница</span>
      </td>)}</tr></tbody>
    </table></div>
    <div className="rp-analytics-comparison-cards">{data.map((item, index) => <article key={item.park_id} className="rp-analytics-card">
      <h3>{parks[index].name}</h3>
      <dl>{metrics.map(key => {
        const metric = key === 'overdue_share' ? item.sla_trend : item.series[key]
        return <div key={key}><dt>{ANALYTICS_LABELS[key]}</dt><dd><strong>{analyticsValue(metric)}</strong><Coverage data={metric} /></dd></div>
      })}<div><dt>Подтверждённые закрытия</dt><dd><strong>{item.verified_closures.count === null ? 'Недоступно' : item.verified_closures.count === 0 ? 'Нет подтверждения' : `≥ ${item.verified_closures.count}`}</strong><span className="rp-analytics-note">Нижняя граница</span></dd></div></dl>
    </article>)}</div>
  </section>
}

function OperationalAnalysis({ data, parks }: { data: HistoricalAnalytics[]; parks: Park[] }) {
  const measured = data.filter(item => item.coverage.flow.observed_buckets > 0 || item.coverage.observations.observed_buckets > 0)
  const assessable = measured.length > 0 && measured.every(item =>
    item.coverage.flow.complete && item.coverage.observations.complete
    && typeof item.sla_trend.value === 'number'
    && typeof item.series.arrived?.value === 'number'
    && typeof item.series.departed?.value === 'number'
    && typeof item.series.backlog?.value === 'number'
    && item.backlog_age_bands.length > 0
    && item.backlog_age_bands.every(band => typeof band.value === 'number'))
  const insights = buildOperationalInsights(measured, parks)
  return <section className="rp-analytics-analysis" aria-labelledby="operational-analysis-title">
    <h2 id="operational-analysis-title">Анализ текущей ситуации</h2>
    <p className="rp-analytics-note">Автоматическая оценка истории очереди, возраста задач, SLA и нагрузки по этапам.</p>
    {insights.length ? <ul>{insights.map((item, index) => <li className={`rp-analytics-insight rp-analytics-insight--${item.severity}`} key={`${item.parkId}-${index}`}>{item.text}</li>)}</ul> : assessable ? <p>Критичных отклонений по доступным данным не обнаружено.</p> : measured.length ? <p>Данных недостаточно для вывода об отклонениях. Проверьте покрытие истории и время входа задач в очередь.</p> : <p>Недостаточно наблюдений для оценки состояния. Дождитесь появления истории процесса.</p>}
  </section>
}

type ParkAnalyticsFailure = { park: Park; failure: DomainError }
type ParkAnalyticsResult = { available: HistoricalAnalytics[]; failed: ParkAnalyticsFailure[] }

function PeriodSnapshot({ data, park }: { data: HistoricalAnalytics; park: Park }) {
  const incomplete = !data.coverage.flow.complete || !data.coverage.observations.complete || !data.verified_closures.complete
  const closures = data.verified_closures.count
  const values = [
    { label: 'Поступило', value: analyticsValue(data.series.arrived) },
    { label: 'Открыто в среднем', value: analyticsValue(data.series.backlog) },
    { label: 'Просрочка SLA', value: data.sla_trend.value === null ? 'Не измерено' : analyticsValue(data.sla_trend) },
    { label: 'Закрыто в Tracker', value: closures === null ? 'Не измерено' : data.verified_closures.complete ? String(closures) : `≥ ${closures}` },
  ]
  return <section aria-label={`Срез периода: ${park.name}`} className="rp-analytics-snapshot">
    <h2>Срез периода · {park.name}</h2>
    <dl>{values.map(item => <div key={item.label}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl>
    {incomplete ? <p className="rp-analytics-note">Неполное покрытие · поток {data.coverage.flow.observed_buckets}/{data.coverage.flow.expected_buckets}, снимки {data.coverage.observations.observed_buckets}/{data.coverage.observations.expected_buckets}. Числа частичные.</p> : null}
  </section>
}

function hasHistoricalEvidence(data: HistoricalAnalytics): boolean {
  return data.coverage.flow.observed_buckets > 0
    || data.coverage.observations.observed_buckets > 0
    || (data.verified_closures.count ?? 0) > 0
    || (data.drilldown_task_keys_count ?? data.drilldown_task_keys.length) > 0
    || [...Object.values(data.series), ...data.backlog_age_bands, data.sla_trend, ...data.stage_durations, ...data.workload]
      .some(metric => metric.sample_count > 0)
}

const historyFailureWarnings = new Set([
  'history_access_denied', 'history_source_unavailable', 'closed_history_search_failed',
  'closed_history_page_cap', 'invalid_history_timezone',
])

function AnalyticsOwner({ apiClient, parks, days, bucket, resourceKey, onAuthorizationFailure, showAnalysis, canConfigure }: {
  apiClient: AnalyticsApiClient; parks: Park[]; days: number; bucket: AnalyticsQuery['bucket']; resourceKey: string; onAuthorizationFailure: (failure: DomainError) => void; showAnalysis: boolean; canConfigure: boolean
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
    const results: Array<HistoricalAnalytics | undefined> = new Array(parks.length)
    const failures: Array<{ failure: DomainError; reason: unknown } | undefined> = new Array(parks.length)
    let next = 0
    let stopped = false
    const active = () => !stopped && generation === ownerGeneration.current
    // Large accessible scopes share three workers; retired scopes never launch queued calls.
    const worker = async () => {
      while (active() && next < parks.length) {
        const index = next++
        const park = parks[index]
        try {
          const outcome = await limitOperationsRequest(async () => {
            if (!active()) throw new Error('analytics_owner_retired')
            try {
              const result = await apiClient.analytics(park.id, days, bucket)
              if (result.park_id !== park.id) throw new Error('analytics_park_mismatch')
              return { result, failure: null }
            } catch (error) {
              // Observe each current request's denial, even after a sibling failed.
              const failure = classifyApiError(error, 'Не удалось загрузить историю процесса.')
              if (failure.kind === 'unauthorized' || failure.kind === 'forbidden') {
                if (generation === ownerGeneration.current) onAuthorizationFailure(failure)
                // Stop this scope before releasing the shared request slot.
                stopped = true
                throw error
              }
              return { result: null, failure: { failure, reason: error } }
            }
          })
          if (!active()) return
          if (outcome.result) results[index] = outcome.result
          else failures[index] = outcome.failure
        } catch (error) {
          stopped = true
          throw error
        }
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(3, parks.length) }, worker))
      if (!active()) throw new Error('analytics_owner_retired')
      const available = results.filter((result): result is HistoricalAnalytics => result !== undefined)
      const failed = failures.flatMap((item, index) => item ? [{ park: parks[index], ...item }] : [])
      // A failed refresh keeps the previous complete snapshot with its visible
      // error banner; an initial load can still show the parks that responded.
      if (failed.length && (!available.length || resourceStore.get<ParkAnalyticsResult>(resourceKey))) throw failed[0].reason
      return { available, failed: failed.map(({ park, failure }) => ({ park, failure })) }
    } finally { activeLoads.current -= 1 }
  }, { persist: false, refreshIntervalMs: 0, refreshOnResume: true, staleTimeMs: 120_000 })
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
  const noHistory = data.available.length > 0 && data.available.every(result =>
    !hasHistoricalEvidence(result) && !result.warnings.some(warning => historyFailureWarnings.has(warning)))
  const emptyPark = data.available.length === 1 ? data.available[0] : null
  return <>
    {failure ? <div className="rp-analytics-warning" role="alert"><strong>{failure.title}</strong><p>{failure.description}</p>{failure.retryable ? <Button variant="secondary" busy={resource.isRevalidating} onClick={() => void resource.refresh()}>Повторить</Button> : null}</div> : null}
    {data.failed.length ? <div className="rp-analytics-warning" role="alert"><strong>Часть парков недоступна</strong><ul>{data.failed.map(({ park, failure }) => <li key={park.id}>{park.name}: {failure.title}. {failure.description}</li>)}</ul><Button variant="secondary" busy={resource.isRevalidating} onClick={() => void resource.refresh()}>Повторить загрузку</Button></div> : null}
    {noHistory ? <section aria-label="Состояние истории процесса">
      <EmptyState
        action={canConfigure && emptyPark ? <Link className="btn" to={`/admin/settings?park=${emptyPark.park_id}&tab=integrations#tracker-token`}>Проверить настройки Tracker</Link> : undefined}
        description="Пока нет подтверждённых наблюдений или закрытий. Проверьте подключение Tracker и очередь парка; показатели появятся после первого опроса задач."
        icon="clock"
        title="История пока не собрана"
      />
      {emptyPark ? <p className="rp-analytics-note">Покрытие: поток {emptyPark.coverage.flow.observed_buckets}/{emptyPark.coverage.flow.expected_buckets}, снимки {emptyPark.coverage.observations.observed_buckets}/{emptyPark.coverage.observations.expected_buckets}.</p> : null}
    </section> : data.available.length === 1 ? <PeriodSnapshot data={data.available[0]} park={parks.find(park => park.id === data.available[0].park_id)!} /> : <Comparison data={data.available} parks={parks.filter(park => data.available.some(item => item.park_id === park.id))} />}
    <div className="rp-analytics-refresh">
      <span className="rp-analytics-note">Устаревшие данные обновятся при возврате.</span>
      <Button variant="secondary" busy={resource.isRevalidating} onClick={() => void resource.refresh()}>Обновить аналитику</Button>
    </div>
    {showAnalysis && !noHistory ? <OperationalAnalysis data={data.available} parks={parks} /> : null}
    {!noHistory ? <div className="rp-analytics-parks">{data.available.map(result => <ParkHistory key={result.park_id} data={result} park={parks.find(park => park.id === result.park_id)!} context={{ apiClient, days, bucket, periodEnd: result.period.end, generatedAt: result.generated_at, onAuthorizationFailure }} />)}</div> : null}
  </>
}

function AnalyticsSession({ apiClient, user }: { apiClient: AnalyticsApiClient; user: User }) {
  const { refreshUser } = useAuth()
  const { selectedPark, parks, loading, allowAllParks } = useParkScope()
  const [params, setParams] = useSearchParams()
  const available = useMemo(() => analyticsParks(user, parks), [user, parks])
  const allParks = Boolean(allowAllParks && !selectedPark)
  const parsedQuery = parseAnalyticsQuery(params, available, selectedPark?.id)
  const query = allParks ? { ...parsedQuery, compare: null } : parsedQuery
  const pendingQuery = useRef(query)
  useLayoutEffect(() => {
    pendingQuery.current = { days: query.days, bucket: query.bucket, compare: query.compare }
  }, [query.days, query.bucket, query.compare, selectedPark?.id])
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
  const update = (patch: Partial<AnalyticsQuery>) => {
    // Router navigation may render later than the next input event. Compose
    // rapid changes from the last intent, not the previous rendered URL.
    const next = { ...pendingQuery.current, ...patch }
    pendingQuery.current = next
    setParams(analyticsSearch(params, next), { replace: true })
  }
  const canRead = user.access_status === 'approved' && !user.must_change_password && ['nav.analytics', 'tracker.read'].every(permission => user.permissions?.includes(permission))
  return <PageLayout title="Аналитика" description="Как меняется процесс и на каких этапах накапливается задержка.">
    {canRead ? <div aria-label="Параметры аналитики" className="rp-analytics-controls rp-analytics-controls--single-row" role="group">
      <label>Период аналитики<select aria-label="Период аналитики" value={query.days} onChange={event => update({ days: Number(event.target.value) })}><option value="1">1 день</option><option value="7">7 дней</option><option value="30">30 дней</option></select></label>
      <label>Шаг графиков<select aria-label="Шаг графиков" value={query.bucket} onChange={event => update({ bucket: event.target.value as AnalyticsQuery['bucket'] })}><option value="1d">24 часа</option><option value="2h">2 часа</option></select></label>
      {!allParks ? <label>Сравнить с парком<select aria-label="Сравнить с парком" value={query.compare ?? ''} onChange={event => update({ compare: event.target.value ? Number(event.target.value) : null })}><option value="">Без сравнения</option>{available.filter(park => park.id !== selectedPark?.id).map(park => <option key={park.id} value={park.id}>{park.name}</option>)}</select></label> : null}
    </div> : null}
    {allParks && canRead ? <h2>Все доступные парки</h2> : null}
    {!canRead ? <ErrorState title="Нет доступа" description="Нужны разрешения на аналитику и чтение Tracker." /> : contextFailure ? <ErrorState title={contextFailure.title} description={contextFailure.description} />
      : loading ? <LoadingState label="Загружаем доступные парки" /> : allParks && !available.length ? <EmptyState title="Нет доступных парков" description="История появится после назначения доступа к активному парку." icon="parks" /> : !allParks && !selectedPark ? <EmptyState title="Парк не выбран" description="Выберите парк для просмотра истории процесса." icon="parks" />
        : selectedPark && !available.some(park => park.id === selectedPark.id) ? <ErrorState title="Нет доступа" description="Выбранный парк недоступен." />
          : <AnalyticsOwner key={identity} resourceKey={`analytics:${user.id}:${identity}`} apiClient={apiClient} parks={requestedParks} days={query.days} bucket={query.bucket} onAuthorizationFailure={onAuthorizationFailure} showAnalysis={user.role === 'operator' || user.role === 'admin' || user.role === 'royal'} canConfigure={user.permissions?.includes('nav.admin') === true} />}
  </PageLayout>
}

export function AnalyticsWorkspace({ apiClient = api }: { apiClient?: AnalyticsApiClient }) {
  const { user } = useAuth()
  return user ? <AnalyticsSession key={user.id} apiClient={apiClient} user={user} /> : null
}
