import { describe, expect, it } from 'vitest'
import { buildOperationalInsights, type HistoricalAnalytics } from './analyticsModel'

describe('operational insights', () => {
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

    const insights = buildOperationalInsights([data], [{ id: 7, name: 'Север', tag: 'North' }])
    expect(insights[0].severity).toBe('critical')
    expect(insights.map(item => item.text).join(' ')).toContain('Север')
    expect(insights.map(item => item.text).join(' ')).toContain('стар')
  })
})
