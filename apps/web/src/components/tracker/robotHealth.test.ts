import { describe, expect, it } from 'vitest'
import type { EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'
import { criticalFindings, robotCheckPathForRobot } from './robotHealth'

function snapshot(overrides: Partial<EmergencySnapshot> = {}): EmergencySnapshot {
  return {
    vin: 'YASADR00000001555',
    short_number: '1555',
    observed_at: '2026-09-02T09:00:00Z',
    online: true,
    speed: 0,
    charge_percent: 80,
    battery1_percent: 80,
    battery2_percent: 80,
    disk_percent: 40,
    mode: 'AUTO',
    icp_label: 'ICP',
    icp_ok: true,
    lte_label: 'LTE',
    lte_ok: true,
    connection: 'lte',
    error_banner: null,
    lat: 55,
    lon: 37,
    heading_deg: 0,
    wheels_fault: [],
    ...overrides,
  }
}

describe('robotHealth', () => {
  it('builds an encoded canonical check link from the robot number', () => {
    expect(robotCheckPathForRobot('a1555')).toBe('/robots/a1555/check')
    expect(robotCheckPathForRobot(' 447 ')).toBe('/robots/447/check')
    expect(robotCheckPathForRobot(' A/42 ?# ')).toBe('/robots/A%2F42%20%3F%23/check')
  })

  it('treats offline, error banner and wheel faults as critical', () => {
    expect(criticalFindings(snapshot())).toEqual([])
    expect(criticalFindings(snapshot({ online: false }))).toEqual([ru.emergency.offlineBanner])
    expect(criticalFindings(snapshot({ error_banner: 'ERROR: MCU' }))).toEqual(['ERROR: MCU'])
    expect(criticalFindings(snapshot({ wheels_fault: ['fl', 'rr'] }))[0]).toContain(
      ru.tracker.robotCheck.wheelsFault,
    )
  })
})
