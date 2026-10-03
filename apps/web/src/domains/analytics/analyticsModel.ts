import type { Park, User } from '../../api'
import { formatDurationHours } from '../../lib/timeFormat'

export type AnalyticsBucket = '2h' | '1d'
export type AnalyticsPeriod = { start: string; end: string }
export type AnalyticsCoverage = { period: AnalyticsPeriod; observed_buckets: number; expected_buckets: number; complete: boolean }
export type AnalyticsMetric = AnalyticsCoverage & {
  key: string
  unit: 'tasks' | 'tasks_per_snapshot' | 'percent' | 'hours'
  value: number | null
  sample_count: number
  task_keys: string[]
  task_keys_count?: number
}
export type AnalyticsSeries = AnalyticsMetric & { aggregation: 'sum' | 'mean' | 'ratio'; points: AnalyticsMetric[] }
export type VerifiedClosureSummary = {
  count: number | null
  task_keys: string[]
  source: 'tracker_status_history'
  complete: false
  sla_on_time_count: number | null
  sla_late_count: number | null
  sla_unknown_count: number | null
  sla_on_time_percent: number | null
  downtime_sample_count: number
  median_downtime_hours: number | null
  p90_downtime_hours: number | null
}
export type HistoricalAnalytics = {
  park_id: number
  generated_at: string
  timezone: string
  period: AnalyticsPeriod
  bucket: AnalyticsBucket
  observation_interval_hours: number
  series: Record<string, AnalyticsSeries>
  backlog_age_bands: AnalyticsSeries[]
  sla_trend: AnalyticsSeries
  stage_durations: AnalyticsMetric[]
  workload: AnalyticsSeries[]
  coverage: Record<string, AnalyticsCoverage>
  drilldown_task_keys: string[]
  drilldown_task_keys_count?: number
  verified_closures: VerifiedClosureSummary
  warnings: string[]
}
export type AnalyticsTaskKeysGroup = 'all' | 'closures' | 'series' | 'age' | 'sla' | 'stage' | 'workload'
export type AnalyticsApiClient = {
  analytics: (parkId: number, days: number, bucket: AnalyticsBucket) => Promise<HistoricalAnalytics>
  analyticsTaskKeys?: (parkId: number, days: number, bucket: AnalyticsBucket, periodEnd: string, group: AnalyticsTaskKeysGroup, key: string | undefined, offset: number, after?: string) => Promise<{ task_keys: string[]; total: number; has_more: boolean }>
}
export type AnalyticsQuery = { days: number; bucket: AnalyticsBucket; compare: number | null }
export type OperationalInsight = { severity: 'critical' | 'warning' | 'info'; text: string; parkId: number }

export const ANALYTICS_LABELS: Record<string, string> = {
  arrived: 'Поступило за период', departed: 'Выбыло за период', backlog: 'Среднее незавершённых',
  under_24h: 'До 24 часов', '24_to_72h': 'От 24 до 72 часов', over_72h: '72 часа и больше', unknown: 'Начало простоя неизвестно',
  new: 'Новые', moving: 'Перемещение', queued: 'Очередь', diagnostics: 'Диагностика',
  waiting_team: 'Ожидание команды', waiting_parts: 'Ожидание запчастей', other: 'Другие этапы', overdue_share: 'Доля просроченных наблюдений',
}
export const ANALYTICS_UNITS: Record<AnalyticsMetric['unit'], string> = {
  tasks: 'задач', tasks_per_snapshot: 'задач / снимок', percent: '%', hours: 'ч',
}
const analyticsNumber = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })
const taskPlural = new Intl.PluralRules('ru-RU', { maximumFractionDigits: 1 })
export function analyticsParks(user: User, parks: Park[]): Park[] {
  return parks.filter(park => park.is_active !== false && (user.role === 'admin' || user.role === 'royal' || user.parks.some(assigned => assigned.id === park.id)))
}
export function analyticsRequestIdentity(user: User, parks: Park[], query: AnalyticsQuery): string {
  const scope = (park: Park) => [park.id, park.tag?.trim(), park.tracker_queue?.trim(), park.timezone, park.is_active !== false]
  return JSON.stringify([
    user.id, user.role, user.access_status, Boolean(user.must_change_password),
    [...new Set(user.permissions ?? [])].sort(), [...user.parks].sort((a, b) => a.id - b.id).map(scope),
    parks.map(scope), query.days, query.bucket,
  ])
}
export function parseAnalyticsQuery(params: URLSearchParams, parks: Park[], selectedId?: number): AnalyticsQuery {
  const period = params.get('period')
  const rawCompare = params.get('compare') ?? ''
  const compare = /^\d+$/.test(rawCompare) ? Number(rawCompare) : null
  return { days: period && ['1', '7', '30'].includes(period) ? Number(period) : 7,
    bucket: params.get('bucket') === '2h' ? '2h' : '1d',
    compare: parks.some(park => park.id === compare && park.id !== selectedId) ? compare : null }
}
export function analyticsSearch(current: URLSearchParams, query: AnalyticsQuery): URLSearchParams {
  const next = new URLSearchParams(current)
  for (const key of ['days', 'status', 'period', 'bucket', 'compare']) next.delete(key)
  if (query.days !== 7) next.set('period', String(query.days))
  if (query.bucket !== '1d') next.set('bucket', query.bucket)
  if (query.compare !== null) next.set('compare', String(query.compare))
  return next
}
export function analyticsValue(metric: AnalyticsMetric): string {
  if (metric.value === null) return 'Нет наблюдений'
  if (metric.unit === 'hours') return formatDurationHours(metric.value)
  const value = analyticsNumber.format(metric.value)
  if (metric.unit === 'tasks' || metric.unit === 'tasks_per_snapshot') {
    const form = taskPlural.select(metric.value)
    const suffix = form === 'one' ? 'задача' : form === 'few' || form === 'other' ? 'задачи' : 'задач'
    return `${value} ${suffix}${metric.unit === 'tasks_per_snapshot' ? ' / снимок' : ''}`
  }
  return `${value} ${ANALYTICS_UNITS[metric.unit]}`
}
export function analyticsDate(value: string, timezone: string): string {
  return new Intl.DateTimeFormat('ru-RU', { timeZone: timezone, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(value))
}

export function buildOperationalInsights(data: HistoricalAnalytics[], parks: Park[]): OperationalInsight[] {
  const names = new Map(parks.map(park => [park.id, park.name]))
  const insights: OperationalInsight[] = []
  for (const result of data) {
    const name = names.get(result.park_id) ?? `Парк ${result.park_id}`
    const over72 = result.backlog_age_bands.find(item => item.key === 'over_72h')?.value
    const overdue = result.sla_trend.value
    const backlog = result.series.backlog
    const points = backlog?.points ?? []
    const previous = points.at(-2)
    const latest = points.at(-1)
    const snapshotEvidence = previous?.complete === true && latest?.complete === true
      && typeof previous.value === 'number' && typeof latest.value === 'number'
      && latest.value > previous.value
      ? `по двум соседним полным снимкам: ${Math.round(previous.value)} → ${Math.round(latest.value)}`
      : null
    const arrived = result.series.arrived?.value
    const departed = result.series.departed?.value
    const flowEvidence = result.coverage?.flow?.complete === true
      && typeof arrived === 'number' && typeof departed === 'number' && arrived > departed
      ? `поступило ${Math.round(arrived)}, выбыло ${Math.round(departed)}`
      : null
    const bottleneck = [...result.workload].filter(item => item.complete === true && typeof item.value === 'number').sort((a, b) => (b.value ?? 0) - (a.value ?? 0))[0]
    if ((typeof overdue === 'number' && overdue >= 40) || (typeof over72 === 'number' && over72 >= 3)) {
      const evidence = [
        typeof overdue === 'number' ? `${Math.round(overdue)}% просроченных наблюдений` : null,
        typeof over72 === 'number' ? `старых задач 72+ ч — ${Math.round(over72)}` : null,
      ].filter(Boolean).join(', ')
      insights.push({ severity: 'critical', parkId: result.park_id, text: `${name}: ${evidence}. Разберите самые старые задачи и снимите внешние блокировки.` })
    }
    if (flowEvidence || snapshotEvidence) {
      const action = bottleneck?.complete === true && typeof bottleneck.value === 'number' && bottleneck.value > 0
        ? `Проверьте нагрузку и доступность исполнителей на этапе «${ANALYTICS_LABELS[bottleneck.key] ?? bottleneck.key}».`
        : 'Проверьте распределение задач и доступность исполнителей.'
      insights.push({ severity: 'warning', parkId: result.park_id, text: `${name}: очередь растёт — ${flowEvidence ?? snapshotEvidence}. ${action}` })
    }
    if (bottleneck?.complete === true && (bottleneck.value ?? 0) > 0) insights.push({ severity: 'info', parkId: result.park_id, text: `${name}: наибольшее накопление — «${ANALYTICS_LABELS[bottleneck.key] ?? bottleneck.key}», в среднем ${bottleneck.value?.toFixed(1)} задачи. Проверьте общую причину задержки.` })
  }
  const rank = { critical: 0, warning: 1, info: 2 }
  return insights.sort((a, b) => rank[a.severity] - rank[b.severity] || a.parkId - b.parkId)
}

/** Separate polylines keep missing observations visibly missing. */
export function trendSegments(points: AnalyticsMetric[]): { x: number; y: number }[][] {
  const max = Math.max(1, ...points.map(point => point.value ?? 0))
  const segments: { x: number; y: number }[][] = []
  let segment: { x: number; y: number }[] = []
  points.forEach((point, index) => {
    if (point.value === null) {
      if (segment.length) segments.push(segment)
      segment = []
    } else segment.push({ x: 8 + index * 304 / Math.max(1, points.length - 1), y: 92 - point.value * 80 / max })
  })
  if (segment.length) segments.push(segment)
  return segments
}
