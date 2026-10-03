import { describe, expect, it } from 'vitest'
import type { EmergencySnapshot } from '../../api'
import { assessRobotReturn } from './robotReturnAssessment'

const now = new Date('2026-09-26T10:00:00Z')
function snapshot(overrides: Partial<EmergencySnapshot> = {}): EmergencySnapshot {
  return {
    vin: 'YASADR00000000447', short_number: '447', observed_at: now.toISOString(),
    online: true, speed: 0, charge_percent: 95,
    battery1_percent: 90, battery2_percent: 90,
    battery1_connected: true, battery2_connected: true,
    disk_percent: 20, mode: 'AUTO', icp_label: 'ICP', icp_ok: true,
    lte_label: 'LTE', lte_ok: true, connection: 'lte', error_banner: null,
    lat: null, lon: null, heading_deg: null, wheels_fault: [], diagnostic_events: [],
    ...overrides,
  }
}

describe('operator robot return assessment', () => {
  it('reports verified checks only when both connected batteries reach 90% and there are no errors', () => {
    expect(assessRobotReturn(snapshot(), { browserOnline: true, now })).toEqual({
      state: 'checks_passed', reasons: [],
    })
  })

  it('identifies each battery below 90% or disconnected as a failed check', () => {
    expect(assessRobotReturn(snapshot({ battery1_percent: 89, battery2_connected: false }), { browserOnline: true, now })).toEqual({
      state: 'attention', reasons: ['АКБ 1 ниже 90%', 'АКБ 2 не подключена'],
    })
  })

  it('treats every marked diagnostic error as attention, whatever its severity', () => {
    const event = { id: 'e1', rule_id: 7, source_path: 'errors.0', source_segments: ['errors', 0], raw_value: 'fault', title: 'Ошибка', description: 'Проверить', severity: 'info' as const, sort_order: 1, part: null, view: null, x: null, y: null, indicator: null }
    expect(assessRobotReturn(snapshot({ diagnostic_events: [event] }), { browserOnline: true, now })).toEqual({
      state: 'attention', reasons: ['Есть размеченные ошибки: 1'],
    })
  })

  it('leaves unknown errors and missing battery data for the operator', () => {
    const event = { id: 'e1', rule_id: null, source_path: 'errors.0', source_segments: ['errors', 0], raw_value: 'fault', title: 'Неизвестная ошибка', description: 'Проверить', severity: 'warning' as const, sort_order: 1, part: null, view: null, x: null, y: null, indicator: null }
    expect(assessRobotReturn(snapshot({ battery2_percent: null, diagnostic_events: [event] }), { browserOnline: true, now })).toEqual({
      state: 'unknown', reasons: ['Нет данных об АКБ 2', 'Неразмеченные ошибки: 1'],
    })
  })

  it('does not accept a charge outside the physical percentage range', () => {
    expect(assessRobotReturn(snapshot({ battery1_percent: 101 }), { browserOnline: true, now })).toEqual({
      state: 'unknown', reasons: ['Нет данных об АКБ 1'],
    })
  })

  it('cannot certify stale, failed or offline readings', () => {
    expect(assessRobotReturn(snapshot({ stale: true }), { browserOnline: true, now }).state).toBe('unknown')
    expect(assessRobotReturn(snapshot(), { browserOnline: true, failed: true, now }).state).toBe('unknown')
    expect(assessRobotReturn(snapshot(), { browserOnline: false, now }).state).toBe('unknown')
    expect(assessRobotReturn(snapshot({ observed_at: '2026-09-26T09:54:59Z' }), { browserOnline: true, now }).state).toBe('unknown')
  })

  it('accepts a one-second clock skew but rejects a reading more than a minute ahead', () => {
    expect(assessRobotReturn(snapshot({ observed_at: '2026-09-26T10:00:01Z' }), { browserOnline: true, now }).state).toBe('checks_passed')
    expect(assessRobotReturn(snapshot({ observed_at: '2026-09-26T10:01:01Z' }), { browserOnline: true, now }).state).toBe('unknown')
  })
})
