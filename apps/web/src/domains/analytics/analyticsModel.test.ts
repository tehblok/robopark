import { describe, expect, it } from 'vitest'
import { analyticsRequestIdentity, analyticsValue, buildOperationalInsights, type AnalyticsMetric, type HistoricalAnalytics } from './analyticsModel'
import { analyticsFixture } from './analytics.test-support'
import { makeUser, park } from '../insights/operations.test-support'

it('refreshes cached analytics when the selected park timezone changes', () => {
  const user = makeUser()
  const query = { days: 7, bucket: '1d' as const, compare: null }
  const before = analyticsRequestIdentity(user, [park], query)
  const after = analyticsRequestIdentity(user, [{ ...park, timezone: 'Asia/Yekaterinburg' }], query)
  expect(after).not.toBe(before)
})

describe('analytics value labels', () => {
  it.each([
    [0, '0 задач'], [1, '1 задача'], [2, '2 задачи'], [5, '5 задач'],
    [21, '21 задача'], [22, '22 задачи'], [1.5, '1,5 задачи'],
  ])('uses Russian task forms for %s', (value, expected) => {
    expect(analyticsValue({ value, unit: 'tasks' } as AnalyticsMetric)).toBe(expected)
    expect(analyticsValue({ value, unit: 'tasks_per_snapshot' } as AnalyticsMetric)).toBe(`${expected} / снимок`)
  })
})

describe('operational insights', () => {
  it('does not recommend a bottleneck from one observed bucket in a seven-day window', () => {
    const insights = buildOperationalInsights([analyticsFixture(7, 7, '1d')], [{ id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North' }])
    expect(insights).toEqual([])
  })
  it('does not invent an SLA percentage when only old-task evidence is available', () => {
    const data = {
      park_id: 7,
      series: { backlog: { value: null, points: [] }, arrived: { value: null }, departed: { value: null } },
      backlog_age_bands: [{ key: 'over_72h', value: 4 }],
      sla_trend: { value: null },
      workload: [],
    } as unknown as HistoricalAnalytics
    const insights = buildOperationalInsights([data], [{ id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North' }])
    expect(insights[0].severity).toBe('critical')
    expect(insights[0].text).not.toContain('0%')
  })

  it('prioritizes growing backlog and old tasks with a concrete recommendation', () => {
    const data = {
      park_id: 7,
      series: {
        backlog: { value: 8, points: [{ value: 3 }, { value: 8 }] },
        arrived: { value: 12, points: [] },
        departed: { value: 5, points: [] },
      },
      backlog_age_bands: [{ key: 'over_72h', value: 4, task_keys: ['RP-1'] }],
      sla_trend: { value: 50 },
      workload: [{ key: 'waiting_parts', value: 3, task_keys: ['RP-2'] }],
    } as unknown as HistoricalAnalytics

    const insights = buildOperationalInsights([data], [{ id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North' }])
    expect(insights[0].severity).toBe('critical')
    expect(insights.map(item => item.text).join(' ')).toContain('Север')
    expect(insights.map(item => item.text).join(' ')).toContain('стар')
  })

  it('does not claim queue growth across a missing observation or incomplete flow', () => {
    const data = {
      park_id: 7,
      coverage: { flow: { complete: false } },
      series: {
        backlog: { value: 8, points: [{ value: 3, complete: true }, { value: null, complete: false }, { value: 8, complete: true }] },
        arrived: { value: 12 },
        departed: { value: 5 },
      },
      backlog_age_bands: [],
      sla_trend: { value: null },
      workload: [],
    } as unknown as HistoricalAnalytics

    const insights = buildOperationalInsights([data], [{ id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North' }])
    expect(insights.some(item => item.text.includes('очередь растёт'))).toBe(false)
  })

  it('reports growth from a complete flow even when snapshots have a gap', () => {
    const data = {
      park_id: 7,
      coverage: { flow: { complete: true } },
      series: {
        backlog: { value: 8, points: [{ value: 3, complete: true }, { value: null, complete: false }, { value: 8, complete: true }] },
        arrived: { value: 12 },
        departed: { value: 5 },
      },
      backlog_age_bands: [],
      sla_trend: { value: null },
      workload: [],
    } as unknown as HistoricalAnalytics

    const insights = buildOperationalInsights([data], [{ id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North' }])
    expect(insights.some(item => item.text.includes('очередь растёт'))).toBe(true)
  })

  it('reports observed growth from two adjacent complete snapshots without full flow history', () => {
    const data = {
      park_id: 7,
      coverage: { flow: { complete: false } },
      series: {
        backlog: { value: 8, points: [{ value: 3, complete: true }, { value: 8, complete: true }] },
        arrived: { value: null },
        departed: { value: null },
      },
      backlog_age_bands: [],
      sla_trend: { value: null },
      workload: [],
    } as unknown as HistoricalAnalytics

    const insights = buildOperationalInsights([data], [{ id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North' }])
    const growth = insights.find(item => item.text.includes('очередь растёт'))?.text
    expect(growth).toContain('3 → 8')
    expect(growth).not.toContain('этап с максимальной нагрузкой')
  })

  it('names the measured stage when recommending where to investigate growth', () => {
    const data = {
      park_id: 7,
      coverage: { flow: { complete: true } },
      series: { backlog: { points: [] }, arrived: { value: 9 }, departed: { value: 4 } },
      backlog_age_bands: [],
      sla_trend: { value: null },
      workload: [{ key: 'queued', value: 9, complete: false }, { key: 'waiting_parts', value: 3, complete: true }],
    } as unknown as HistoricalAnalytics

    const insights = buildOperationalInsights([data], [{ id: 7, name: 'Север', timezone: 'Europe/Moscow', tag: 'North' }])
    expect(insights.find(item => item.severity === 'warning')?.text).toContain('Ожидание запчастей')
  })
})
