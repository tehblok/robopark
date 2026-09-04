import { describe, expect, it } from 'vitest'
import type { EmergencySnapshot } from '../../api'
import { buildRobotDetailModel } from './robotDetailModel'

const now = new Date('2026-09-02T09:05:00Z')
function snapshot(overrides: Partial<EmergencySnapshot> = {}): EmergencySnapshot {
  return {
    vin: 'YASADR00000000447', short_number: '447', observed_at: '2026-09-02T09:05:00Z', online: true,
    speed: 0, charge_percent: 80, battery1_percent: 80, battery2_percent: 80, disk_percent: 20,
    mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true,
    connection: 'lte', error_banner: null, lat: 55, lon: 37, heading_deg: 0, wheels_fault: [], ...overrides,
  }
}
describe('robot detail model', () => {
  it.each([
    [false, true, 'device-offline', 'warning'], [true, false, 'robot-offline', 'critical'],
    [true, true, 'online', 'success'], [true, null, 'unknown', 'neutral'],
  ] as const)('separates browser %s and robot %s connection', (browserOnline, online, state, tone) => {
    expect(buildRobotDetailModel(snapshot({ online }), browserOnline, now).connection).toMatchObject({ state, tone })
  })
  it.each([
    ['2026-09-02T09:04:30Z', 'live'], ['2026-09-02T09:04:29Z', 'fresh'],
    ['2026-09-02T09:00:00Z', 'fresh'], ['2026-09-02T08:59:59Z', 'stale'], ['invalid', 'stale'],
  ] as const)('ages observation %s as %s', (observed_at, expected) => {
    expect(buildRobotDetailModel(snapshot({ observed_at }), true, now).freshness).toBe(expected)
  })
  it('marks retained data offline and exposes real critical reasons only', () => {
    expect(buildRobotDetailModel(snapshot(), false, now).freshness).toBe('offline')
    expect(buildRobotDetailModel(snapshot(), true, now).criticalReason).toBeNull()
    expect(buildRobotDetailModel(snapshot({ wheels_fault: ['FL'] }), true, now).criticalReason).toBe('Обнаружена неисправность колёс')
    expect(buildRobotDetailModel(snapshot({ error_banner: 'Аварийная остановка', wheels_fault: ['FL'] }), true, now).criticalReason).toBe('Аварийная остановка')
  })
})
