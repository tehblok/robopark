import type { AnalyticsBucket, AnalyticsMetric, AnalyticsSeries, HistoricalAnalytics } from './analyticsModel'

export function analyticsFixture(parkId = 7, days = 1, bucket: AnalyticsBucket = '2h'): HistoricalAnalytics {
  const end = Date.parse('2026-09-06T12:00:00Z')
  const start = end - days * 24 * 60 * 60 * 1000
  const period = { start: new Date(start).toISOString(), end: new Date(end).toISOString() }
  const expected = days * 12
  const coverage = { period, observed_buckets: 1, expected_buckets: expected, complete: false }
  const metric: AnalyticsMetric = { ...coverage, key: 'backlog', unit: 'tasks_per_snapshot', value: 2, sample_count: 2, task_keys: ['ROBOPARK-42'] }
  const step = (bucket === '2h' ? 2 : 24) * 60 * 60 * 1000
  const points: AnalyticsMetric[] = Array.from({ length: (end - start) / step }, (_, index) => {
    const observed = index === (end - start) / step - 1
    return { ...metric, period: { start: new Date(start + index * step).toISOString(), end: new Date(start + (index + 1) * step).toISOString() }, expected_buckets: bucket === '2h' ? 1 : 12,
      observed_buckets: observed ? 1 : 0, complete: observed && bucket === '2h', value: observed ? 2 : null, sample_count: observed ? 2 : 0, task_keys: observed ? ['ROBOPARK-42'] : [] }
  })
  const series: AnalyticsSeries = { ...metric, aggregation: 'mean', points }
  const flow = (key: string): AnalyticsSeries => ({ ...series, key, unit: 'tasks', aggregation: 'sum', task_keys: [], points: points.map(point => ({ ...point, key, unit: 'tasks', task_keys: [] })) })
  return {
    park_id: parkId, generated_at: period.end, timezone: 'Europe/Moscow', period, bucket, observation_interval_hours: 2,
    series: { arrived: flow('arrived'), departed: flow('departed'), backlog: series },
    backlog_age_bands: [{ ...series, key: 'under_24h' }],
    sla_trend: { ...series, key: 'overdue_share', unit: 'percent', value: null, sample_count: 0, observed_buckets: 0, task_keys: [], aggregation: 'ratio', points: points.map(point => ({ ...point, key: 'overdue_share', unit: 'percent', value: null, sample_count: 0, observed_buckets: 0, complete: false, task_keys: [] })) },
    stage_durations: [{ ...metric, key: 'queued', unit: 'hours', value: null, sample_count: 0, task_keys: [] }],
    workload: [{ ...series, key: 'queued' }], coverage: { flow: coverage, observations: coverage }, drilldown_task_keys: ['ROBOPARK-42'],
    verified_closures: { count: 1, task_keys: ['ROBOPARK-42'], source: 'tracker_status_history', complete: false, sla_on_time_count: 1, sla_late_count: 0, sla_unknown_count: 0, sla_on_time_percent: 100, downtime_sample_count: 1, median_downtime_hours: 4, p90_downtime_hours: 4 },
    warnings: ['flow_history_incomplete', 'observations_incomplete', 'queue_history_unavailable', 'stage_durations_are_observed_estimates', 'flow_counts_have_no_task_keys'],
  }
}
