import { describe, expect, it } from 'vitest'
import type { OperationsOverview } from '../../api'
import { buildOverviewModel } from './overviewModel'

const operationsSnapshot: OperationsOverview = {
  park_id: 7,
  generated_at: '2026-09-02T09:00:00Z',
  timezone: 'Europe/Moscow',
  selected_status: 'all',
  status_options: [
    { key: 'all', label: 'Все доступные' },
    { key: 'new', label: 'Новые' },
    { key: 'moving', label: 'Перемещение' },
    { key: 'queued', label: 'Очередь' },
    { key: 'diagnostics', label: 'Диагностика' },
    { key: 'waiting_team', label: 'Ожидает команду' },
  ],
  counts: { all: 5, new: 2, moving: 1, queued: 1, diagnostics: 1, waiting_team: 0 },
  tasks: [
    { key: 'RP-OLD', summary: 'Старая задача', status: 'Новая', bucket: 'new', robot: '447', created_at: '2026-09-01T09:00:00Z', hours_created: '24', url: '' },
    { key: 'RP-RECENT', summary: 'Свежая задача', status: 'Очередь', bucket: 'queued', robot: null, created_at: '2026-09-02T08:00:00Z', hours_created: '1', url: '' },
  ],
  tasks_total: 2,
  tasks_truncated: false,
  flow: {
    definition_version: 2,
    window_start: '2026-09-02T03:00:00Z',
    window_end: '2026-09-02T09:00:00Z',
    expected_buckets: 3,
    observed_buckets: 2,
    complete: false,
    legacy_buckets: 0,
    points: [
      { bucket_start: '2026-09-02T03:00:00Z', arrived_count: 1, departed_count: 0 },
      { bucket_start: '2026-09-02T07:00:00Z', arrived_count: 0, departed_count: 1 },
    ],
  },
  sla: {
    target_hours: 5,
    evaluated_count: 2,
    unknown_count: 0,
    at_risk_count: 1,
    overdue_count: 1,
    overdue: [{ key: 'RP-OVERDUE', summary: 'Просроченная задача', status: 'Новая', bucket: 'new', robot: '448', created_at: '2026-09-01T00:00:00Z', hours_created: '33', url: '', age_hours: 33, overdue_hours: 28 }],
    overdue_truncated: false,
  },
  workload: [{ login: 'operator', display: 'Оператор смены', open_count: 2, overdue_count: 1, oldest_hours: 33 }],
  operators: [{ user_id: 5, username: 'operator', tracker_login: 'operator', open_count: 2, overdue_count: 1, oldest_hours: 33 }],
}

describe('role-aware operational overview', () => {
  it('advances the SLA locally in the confirmed anchor timezone and freezes overnight', () => {
    const data: OperationsOverview = { ...operationsSnapshot, timezone: 'Asia/Yekaterinburg',
      sla: { ...operationsSnapshot.sla, overdue: [] },
      task_timing: [{ issue_key: 'RP-RECENT', queue_started_at: '2026-09-02T17:00:00Z',
        sla_deadline: '2026-09-03T10:00:00Z', sla_timezone: 'Europe/Moscow', sla_working_hours: 1, downtime_hours: 1 }],
    }
    expect(buildOverviewModel(data, 'mechanic', Date.parse('2026-09-02T19:00:00Z')).attentionQueue[0].slaWorkingHours).toBe(1)
    expect(buildOverviewModel(data, 'mechanic', Date.parse('2026-09-03T05:00:00Z')).attentionQueue[0].slaWorkingHours).toBe(1)
    expect(buildOverviewModel(data, 'mechanic', Date.parse('2026-09-03T07:00:00Z')).attentionQueue[0].slaWorkingHours).toBe(2)
    expect(buildOverviewModel(data, 'mechanic', Date.parse('2026-09-03T10:01:00Z')).attentionQueue[0].kind).toBe('overdue')
  })
  it('builds a compact shift summary from the existing scoped response', () => {
    const model = buildOverviewModel(operationsSnapshot, 'operator')
    expect(model.headline).toEqual({
      active: operationsSnapshot.counts.all,
      overdue: 1,
      atRisk: 1,
      unknownSla: 0,
    })
  })

  it('keeps unavailable headline measures unknown', () => {
    const model = buildOverviewModel({
      ...operationsSnapshot,
      counts: {},
      sla: { ...operationsSnapshot.sla, overdue_count: null, at_risk_count: null, unknown_count: 2 },
    }, 'mechanic')
    expect(model.headline).toEqual({ active: null, overdue: null, atRisk: null, unknownSla: 2 })
  })

  it('describes missing SLA data without suggesting that the fixed five-hour policy is configurable', () => {
    const model = buildOverviewModel({
      ...operationsSnapshot,
      sla: { ...operationsSnapshot.sla, target_hours: null, overdue_count: null, at_risk_count: null, overdue: [] },
    }, 'operator')

    expect(model.alerts).toContainEqual(expect.objectContaining({
      title: 'Данные SLA недоступны',
    }))
    expect(model.alerts.some(alert => alert.description.includes('задан норматив'))).toBe(false)
  })

  it('shows drivers only new and moving status monitoring', () => {
    const model = buildOverviewModel(operationsSnapshot, 'driver')

    expect(model.statusCards.map((card) => card.key)).toEqual(['new', 'moving'])
  })

  it('shows mechanics only queued and diagnostics status monitoring', () => {
    const model = buildOverviewModel(operationsSnapshot, 'mechanic')

    expect(model.statusCards.map((card) => card.key)).toEqual(['queued', 'diagnostics'])
    expect(model.statusCards.map((card) => card.label)).toEqual(['Очередь', 'Диагностика'])
  })

  it.each(['operator', 'admin', 'royal'])('lets privileged %s select all permitted task statuses', (role) => {
    const model = buildOverviewModel(operationsSnapshot, role)

    expect(model.statusCards.map((card) => card.key)).toEqual(['new', 'moving', 'queued', 'diagnostics', 'waiting_team'])
  })

  it('puts SLA overdue tasks before merely old attention items', () => {
    const model = buildOverviewModel(operationsSnapshot, 'operator')

    expect(model.attentionQueue.map((item) => item.key)).toEqual(['RP-OVERDUE', 'RP-OLD', 'RP-RECENT'])
    expect(model.alerts[0]).toMatchObject({ tone: 'critical', taskCount: 1 })
  })

  it('does not present a truncated overdue list as the total overdue count', () => {
    const model = buildOverviewModel({
      ...operationsSnapshot,
      sla: {
        ...operationsSnapshot.sla,
        overdue_count: 8,
        overdue_truncated: true,
      },
    }, 'operator')

    expect(model.headline.overdue).toBe(8)
    expect(model.attentionQueue.filter(item => item.kind === 'overdue')).toHaveLength(1)
    expect(model.alerts[0].title).toContain('Показано просроченных: 1')
    expect(model.alerts[0].description).toContain('неполный')
  })

  it('keeps unknown queue times in source order instead of sorting by ticket creation age', () => {
    const base = operationsSnapshot.tasks[0]
    const data = {
      ...operationsSnapshot,
      sla: { ...operationsSnapshot.sla, overdue: [], overdue_count: 0 },
      tasks: [
        { ...base, key: 'RP-UNKNOWN-NEW', hours_created: '1' },
        { ...base, key: 'RP-KNOWN', hours_created: '2' },
        { ...base, key: 'RP-UNKNOWN-OLD', hours_created: '500' },
      ],
      task_timing: [{ issue_key: 'RP-KNOWN', queue_started_at: '2026-09-01T08:00:00Z', sla_deadline: '2026-09-01T13:00:00Z', sla_working_hours: 2, downtime_hours: 4 }],
    }
    expect(buildOverviewModel(data, 'operator').attentionQueue.map(item => item.key)).toEqual([
      'RP-KNOWN', 'RP-UNKNOWN-NEW', 'RP-UNKNOWN-OLD',
    ])
  })

  it('prioritizes confirmed SLA risk over longer calendar downtime', () => {
    const base = operationsSnapshot.tasks[0]
    const data = {
      ...operationsSnapshot,
      sla: { ...operationsSnapshot.sla, overdue: [], overdue_count: 0 },
      tasks: [
        { ...base, key: 'RP-ROUTINE' },
        { ...base, key: 'RP-RISK' },
      ],
      task_timing: [
        { issue_key: 'RP-ROUTINE', queue_started_at: '2026-08-31T00:00:00Z', sla_deadline: '2026-09-03T00:00:00Z', sla_working_hours: 2, downtime_hours: 48 },
        { issue_key: 'RP-RISK', queue_started_at: '2026-09-02T05:00:00Z', sla_deadline: '2026-09-02T10:00:00Z', sla_working_hours: 4, downtime_hours: 4 },
      ],
    }
    const queue = buildOverviewModel(data, 'operator').attentionQueue
    expect(queue.map(item => item.key)).toEqual(['RP-RISK', 'RP-ROUTINE'])
    expect(queue[0].kind).toBe('at_risk')
  })

  it('keeps a measured overdue task urgent when the backend overdue list is truncated', () => {
    const data = {
      ...operationsSnapshot,
      sla: { ...operationsSnapshot.sla, overdue: [], overdue_truncated: true },
      tasks: [{ ...operationsSnapshot.tasks[0], key: 'RP-LATE' }],
      task_timing: [{ issue_key: 'RP-LATE', queue_started_at: '2026-09-01T00:00:00Z', sla_deadline: '2026-09-01T11:00:00Z', sla_working_hours: 6, downtime_hours: 30 }],
    }

    const model = buildOverviewModel(data, 'operator')
    expect(model.attentionQueue[0]).toMatchObject({
      key: 'RP-LATE', kind: 'overdue', overdueHours: 1,
    })
    expect(model.alerts[0].title).toBe('Показано просроченных: 1')
  })

  it('keeps a 21:00 deadline urgent overnight when the backend overdue list is truncated', () => {
    const data = {
      ...operationsSnapshot,
      generated_at: '2026-09-18T18:01:00Z',
      sla: { ...operationsSnapshot.sla, overdue: [], overdue_count: 1, overdue_truncated: true },
      tasks: [{ ...operationsSnapshot.tasks[0], key: 'RP-NIGHT' }],
      task_timing: [{ issue_key: 'RP-NIGHT', queue_started_at: '2026-09-18T13:00:00Z', sla_deadline: '2026-09-18T18:00:00Z', sla_working_hours: 5, downtime_hours: 5.02 }],
    }

    expect(buildOverviewModel(data, 'operator').attentionQueue[0]).toMatchObject({
      key: 'RP-NIGHT', kind: 'overdue', overdueHours: 0,
    })
  })

  it('shows verified queue downtime and working SLA separately without using task creation age', () => {
    const model = buildOverviewModel({
      ...operationsSnapshot,
      task_timing: [
        { issue_key: 'RP-OVERDUE', queue_started_at: '2026-09-01T00:00:00Z', sla_deadline: '2026-09-01T11:00:00Z', sla_working_hours: 6, downtime_hours: 33 },
        { issue_key: 'RP-RECENT', queue_started_at: '2026-09-02T08:00:00Z', sla_deadline: '2026-09-02T13:00:00Z', sla_working_hours: 1, downtime_hours: 1 },
      ],
    }, 'operator')

    expect(model.attentionQueue.find((item) => item.key === 'RP-OVERDUE')).toMatchObject({
      downtimeHours: 33, slaWorkingHours: 6,
    })
    expect(model.attentionQueue.find((item) => item.key === 'RP-OLD')).toMatchObject({
      downtimeHours: null, slaWorkingHours: null,
    })
  })

  it('carries a verified park-time deadline and current assignee into the queue', () => {
    const data = {
      ...operationsSnapshot,
      tasks: [{ ...operationsSnapshot.tasks[0], assignee: { display: 'Механик А', login: 'mech-a' } }, operationsSnapshot.tasks[1]],
      task_timing: [{ issue_key: 'RP-OLD', queue_started_at: '2026-09-01T08:00:00Z', sla_deadline: '2026-09-01T13:00:00Z', sla_working_hours: 2, downtime_hours: 4 }],
    }
    const model = buildOverviewModel(data, 'operator')
    expect(model.timezone).toBe(operationsSnapshot.timezone)
    expect(model.attentionQueue.find(item => item.key === 'RP-OLD')).toMatchObject({
      assignee: 'Механик А', slaDeadline: '2026-09-01T13:00:00Z',
    })
    expect(model.attentionQueue.find(item => item.key === 'RP-RECENT')).toMatchObject({
      assignee: null, slaDeadline: null,
    })
  })

  it('keeps the selected park and status when opening the paginated work queue', () => {
    expect(buildOverviewModel(operationsSnapshot, 'operator').fullQueueHref).toBe('/work?park=7&status=all')
    expect(buildOverviewModel({ ...operationsSnapshot, selected_status: 'diagnostics' }, 'operator').fullQueueHref)
      .toBe('/work?park=7&status=diagnostics')
  })

  it('does not leak an overdue task from another selected status into attention', () => {
    const model = buildOverviewModel({
      ...operationsSnapshot,
      selected_status: 'moving',
      tasks: [{ ...operationsSnapshot.tasks[0], key: 'RP-MOVING', bucket: 'moving' }],
      sla: {
        ...operationsSnapshot.sla,
        overdue_count: 2,
        overdue: [
          { ...operationsSnapshot.sla.overdue[0], key: 'RP-OVERDUE-QUEUED', bucket: 'queued' },
          { ...operationsSnapshot.sla.overdue[0], key: 'RP-OVERDUE-MOVING', bucket: 'moving' },
        ],
      },
    }, 'operator')

    expect(model.attentionQueue.map((item) => item.key)).toEqual(['RP-OVERDUE-MOVING', 'RP-MOVING'])
    expect(model.alerts[0]).toMatchObject({ taskCount: 1 })
  })

  it('keeps arrived and left unknown when no flow interval was observed', () => {
    const model = buildOverviewModel({
      ...operationsSnapshot,
      flow: { ...operationsSnapshot.flow, observed_buckets: 0, complete: false, points: [] },
    }, 'operator')

    expect(model.flow.arrivedTaskCount).toBeNull()
    expect(model.flow.leftTaskCount).toBeNull()
  })
})
