import { describe, expect, it } from 'vitest'
import type { EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'
import { criticalFindings, emergencyPathForRobot } from './robotHealth'

function snapshot(overrides: Partial<EmergencySnapshot> = {}): EmergencySnapshot {
  return {
    vin: 'YASADR00000001555',
    short_number: '1555',
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
  it('builds an Emergency deep-link from the robot number', () => {
    expect(emergencyPathForRobot('a1555')).toBe('/emergency?q=a1555')
    expect(emergencyPathForRobot(' 447 ')).toBe('/emergency?q=447')
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
