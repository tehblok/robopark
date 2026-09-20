import { describe, expect, it } from 'vitest'
import type { DashboardSummary, OperationsOverview, Park, TrackerIssue } from '../../api'
import type { OverviewPayload } from './overviewData'
import { buildOverviewModel } from './overviewModel'

const park: Park = { id: 7, name: 'Север', tag: 'Alpha', tracker_queue: 'ROBOPARK' }
const summary: DashboardSummary = {
  park_id: 7,
  generated_at: '2026-09-02T09:00:00Z',
  arrived: 2,
  done: 4,
  queued: 3,
  in_transit: 1,
  moving: [{ key: 'ROBOPARK-42', summary: 'Робот 447 остановился' }],
}
const issue: TrackerIssue = {
  key: 'ROBOPARK-99',
  summary: 'Проверить колесо',
  status: 'Open',
  robot: '448',
  url: 'https://st.yandex-team.ru/ROBOPARK-99',
}
const now = new Date('2026-09-02T09:03:00Z')

function parkPayload(overrides: Partial<DashboardSummary> = {}): OverviewPayload {
  return { kind: 'park', park, summary: { ...summary, ...overrides }, issues: [] }
}

describe('buildOverviewModel', () => {
  it.each([
    ['mechanic', 'Что требует внимания в смене', 'Открыть задачу ROBOPARK-42'],
    ['operator', 'Что мешает работе парка', 'Разобрать ROBOPARK-42'],
    ['admin', 'Готовность людей и системы', 'Открыть работу'],
    ['field_lead', 'Что требует внимания сейчас', 'Открыть ROBOPARK-42'],
    ['constructor', 'Что требует внимания сейчас', 'Открыть ROBOPARK-42'],
    ['__proto__', 'Что требует внимания сейчас', 'Открыть ROBOPARK-42'],
  ])('builds a guarded %s hierarchy with a canonical action', (role, title, label) => {
    const model = buildOverviewModel({
      role,
      payload: parkPayload(),
      parkId: 7,
      canOpenAdministration: false,
    }, now)

    expect(model.scope).toBe('Парк: Север')
    expect(model.state.title).toBe(title)
    expect(model.primaryAction).toEqual({
      label,
      href: '/work/ROBOPARK-42?park=7&queue=ROBOPARK&status=all',
      icon: 'work',
    })
    expect(model.updatedAt).toBe('2026-09-02T09:00:00Z')
    expect(model.freshness).toBe('fresh')
  })

  it('gives the driver a robot-first path without invented data or freshness', () => {
    const model = buildOverviewModel({
      role: 'driver',
      payload: { kind: 'driver' },
      parkId: null,
      canOpenAdministration: false,
    }, now)

    expect(model.scope).toContain('проверка конкретного робота')
    expect(model.state.title).toBe('Можно ли безопасно продолжать работу')
    expect(model.updatedAt).toBeNull()
    expect(model.freshness).toBeNull()
    expect(model.risk).toBeNull()
    expect(model.queue).toEqual([])
    expect(model.metrics).toEqual([])
    expect(model.primaryAction).toEqual({
      label: 'Найти или сканировать робота',
      href: '/robots',
      icon: 'scan',
    })
  })

  it.each([
    ['2026-09-02T09:00:30.000Z', 'live'],
    ['2026-09-02T09:00:30.001Z', 'fresh'],
    ['2026-09-02T09:05:00.000Z', 'fresh'],
    ['2026-09-02T09:05:00.001Z', 'stale'],
  ] as const)('classifies the exact freshness boundary at %s as %s', (timestamp, expected) => {
    const model = buildOverviewModel({
      role: 'operator',
      payload: parkPayload(),
      parkId: 7,
      canOpenAdministration: false,
    }, new Date(timestamp))

    expect(model.freshness).toBe(expected)
  })

  it('does not invent a timestamp or freshness for malformed observation metadata', () => {
    const model = buildOverviewModel({
      role: 'operator',
      payload: parkPayload({ generated_at: 'not-a-date' }),
      parkId: 7,
      canOpenAdministration: false,
    }, now)

    expect(model.updatedAt).toBeNull()
    expect(model.freshness).toBeNull()
  })

  it('prefers the moving issue while keeping the supplied operational queue and current metrics', () => {
    const model = buildOverviewModel({
      role: 'mechanic',
      payload: { kind: 'park', park, summary, issues: [issue] },
      parkId: 999,
      canOpenAdministration: false,
    }, now)

    expect(model.primaryAction?.href).toBe('/work/ROBOPARK-42?park=7&queue=ROBOPARK&status=all')
    expect(model.risk?.issueKey).toBe('ROBOPARK-42')
    expect(model.queue).toEqual([{
      key: 'ROBOPARK-99',
      summary: 'Проверить колесо',
      robot: '448',
      href: '/work/ROBOPARK-99?park=7&queue=ROBOPARK&status=all',
    }])
    expect(model.metrics).toEqual([
      { label: 'Пришли', value: 2 },
      { label: 'Завершены', value: 4 },
      { label: 'В очереди', value: 3 },
      { label: 'В пути', value: 1 },
    ])
  })

  it('uses the first queue issue when no moving issue is available', () => {
    const model = buildOverviewModel({
      role: 'operator',
      payload: { kind: 'park', park, summary: { ...summary, moving: [] }, issues: [issue] },
      parkId: 7,
      canOpenAdministration: false,
    }, now)

    expect(model.primaryAction).toEqual({
      label: 'Разобрать ROBOPARK-99',
      href: '/work/ROBOPARK-99?park=7&queue=ROBOPARK&status=all',
      icon: 'work',
    })
    expect(model.risk?.issueKey).toBe('ROBOPARK-99')
  })

  it('keeps non-zero counts informational when there is no actionable issue', () => {
    const model = buildOverviewModel({
      role: 'admin',
      payload: parkPayload({ moving: [] }),
      parkId: 999,
      canOpenAdministration: true,
    }, now)

    expect(model.state.tone).toBe('neutral')
    expect(model.risk).toBeNull()
    expect(model.primaryAction).toEqual({
      label: 'Открыть работу',
      href: '/work?park=7&queue=ROBOPARK&status=all',
      icon: 'work',
    })
  })

  it('encodes issue keys and trims the Tracker queue without replacing the numeric park', () => {
    const model = buildOverviewModel({
      role: 'field_lead',
      payload: {
        kind: 'park',
        park: { ...park, tracker_queue: ' TEAM & OPS ' },
        summary: { ...summary, moving: [] },
        issues: [{ ...issue, key: 'Q/42 ?#' }],
      },
      parkId: 999,
      canOpenAdministration: false,
    }, now)

    expect(model.primaryAction?.href).toBe('/work/Q%2F42%20%3F%23?park=7&queue=TEAM+%26+OPS&status=all')
    expect(model.queue[0]?.href).toBe('/work/Q%2F42%20%3F%23?park=7&queue=TEAM+%26+OPS&status=all')
  })

  it.each([
    { tracker_queue: undefined },
    { tracker_queue: null },
    { tracker_queue: '' },
    { tracker_queue: '   ' },
    { tag: '' },
    { tag: '   ' },
  ])('shows text-only readiness guidance for missing Tracker config %j', (config) => {
    const model = buildOverviewModel({
      role: 'admin',
      payload: { kind: 'park', park: { ...park, ...config }, summary, issues: [] },
      parkId: 7,
      canOpenAdministration: false,
    }, now)

    expect(model.risk).toMatchObject({
      tone: 'warning',
      title: 'Парк не готов к работе с Tracker',
    })
    expect(model.risk?.description).toMatch(/администратор/i)
    expect(model.primaryAction).toBeNull()
  })

  it('offers Administration only from the explicit capability, including a custom role', () => {
    const model = buildOverviewModel({
      role: 'field_lead',
      payload: { kind: 'park', park: { ...park, tracker_queue: null }, summary, issues: [] },
      parkId: 7,
      canOpenAdministration: true,
    }, now)

    expect(model.primaryAction).toEqual({
      label: 'Настроить парк',
      href: '/admin',
      icon: 'settings',
    })
  })

  it('uses the oldest fleet observation and highest current queue without changing the payload', () => {
    const south: Park = { ...park, id: 8, name: 'Юг', tag: 'Beta' }
    const payload: OverviewPayload = {
      kind: 'fleet',
      summaries: [
        { park, summary: { ...summary, generated_at: '2026-09-02T08:59:00Z' } },
        {
          park: south,
          summary: {
            ...summary,
            park_id: 8,
            generated_at: '2026-09-02T11:57:59+03:00',
            queued: 9,
            moving: [{ key: 'ROBOPARK-88', summary: 'Робот в пути' }],
          },
        },
      ],
    }
    const original = structuredClone(payload)
    Object.freeze(payload.summaries)

    const model = buildOverviewModel({
      role: 'royal',
      payload,
      parkId: 7,
      canOpenAdministration: true,
    }, now)

    expect(model.scope).toBe('Все доступные активные парки · 2')
    expect(model.state.title).toBe('Главный риск доступных парков')
    expect(model.updatedAt).toBe('2026-09-02T11:57:59+03:00')
    expect(model.freshness).toBe('stale')
    expect(model.risk).toMatchObject({
      title: 'Наибольшая текущая очередь: Юг · 9',
      tone: 'info',
    })
    expect(model.primaryAction?.href).toBe('/work?park=8&status=all')
    expect(model.metrics).toEqual([
      { label: 'Пришли', value: 4 },
      { label: 'Завершены', value: 8 },
      { label: 'В очереди', value: 12 },
      { label: 'В пути', value: 2 },
    ])
    expect(model.queue).toEqual([
      {
        key: 'ROBOPARK-42',
        summary: 'Робот 447 остановился',
        href: '/work/ROBOPARK-42?park=7&queue=ROBOPARK&status=all',
      },
      {
        key: 'ROBOPARK-88',
        summary: 'Робот в пути',
        href: '/work/ROBOPARK-88?park=8&queue=ROBOPARK&status=all',
      },
    ])
    expect(payload).toEqual(original)
  })

  it('keeps the first accessible park as the deterministic tie-breaker for equal fleet queues', () => {
    const model = buildOverviewModel({
      role: 'royal',
      payload: {
        kind: 'fleet',
        summaries: [
          { park: { ...park, id: 8, name: 'Юг' }, summary: { ...summary, park_id: 8 } },
          { park, summary },
        ],
      },
      parkId: 7,
      canOpenAdministration: true,
    }, now)

    expect(model.primaryAction?.href).toBe('/work?park=8&status=all')
  })

  it('keeps an empty fleet truthful without fabricated metrics, freshness or a park action', () => {
    const model = buildOverviewModel({
      role: 'royal',
      payload: { kind: 'fleet', summaries: [] },
      parkId: 7,
      canOpenAdministration: true,
    }, now)

    expect(model.scope).toBe('Все доступные активные парки · 0')
    expect(model.updatedAt).toBeNull()
    expect(model.freshness).toBeNull()
    expect(model.risk).toBeNull()
    expect(model.primaryAction).toBeNull()
    expect(model.queue).toEqual([])
    expect(model.metrics).toEqual([])
  })
})

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
    target_hours: 8,
    evaluated_count: 2,
    unknown_count: 0,
    at_risk_count: 1,
    overdue_count: 1,
    overdue: [{ key: 'RP-OVERDUE', summary: 'Просроченная задача', status: 'Новая', bucket: 'new', robot: '448', created_at: '2026-09-01T00:00:00Z', hours_created: '33', url: '', age_hours: 33, overdue_hours: 25 }],
    overdue_truncated: false,
  },
  workload: [{ login: 'operator', display: 'Оператор смены', open_count: 2, overdue_count: 1, oldest_hours: 33 }],
  operators: [{ user_id: 5, username: 'operator', tracker_login: 'operator', open_count: 2, overdue_count: 1, oldest_hours: 33 }],
}

describe('role-aware operational overview', () => {
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
