import { describe, expect, it } from 'vitest'
import type { DashboardSummary, Park, TrackerIssue } from '../../api'
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
      href: '/work/ROBOPARK-42?park=7&queue=ROBOPARK',
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

    expect(model.primaryAction?.href).toBe('/work/ROBOPARK-42?park=7&queue=ROBOPARK')
    expect(model.risk?.issueKey).toBe('ROBOPARK-42')
    expect(model.queue).toEqual([{
      key: 'ROBOPARK-99',
      summary: 'Проверить колесо',
      robot: '448',
      href: '/work/ROBOPARK-99?park=7&queue=ROBOPARK',
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
      href: '/work/ROBOPARK-99?park=7&queue=ROBOPARK',
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
      href: '/work?park=7&queue=ROBOPARK',
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

    expect(model.primaryAction?.href).toBe('/work/Q%2F42%20%3F%23?park=7&queue=TEAM+%26+OPS')
    expect(model.queue[0]?.href).toBe('/work/Q%2F42%20%3F%23?park=7&queue=TEAM+%26+OPS')
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
    expect(model.primaryAction?.href).toBe('/work?park=8')
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
        href: '/work/ROBOPARK-42?park=7&queue=ROBOPARK',
      },
      {
        key: 'ROBOPARK-88',
        summary: 'Робот в пути',
        href: '/work/ROBOPARK-88?park=8&queue=ROBOPARK',
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

    expect(model.primaryAction?.href).toBe('/work?park=8')
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
