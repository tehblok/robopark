import { describe, expect, it } from 'vitest'
import type { EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'
import {
  formatHudPercent,
  formatHudSpeed,
  metricTone,
  robotHudBatteries,
  robotHudConnection,
  robotHudConnectionLabel,
  robotHudStatus,
  robotHudStatusLabel,
  wheelFaultLabel,
  wheelFaultSide,
} from './robotHud'

function snapshot(overrides: Partial<EmergencySnapshot> = {}): EmergencySnapshot {
  return {
    vin: 'YASADR00000001975',
    short_number: '1975',
    online: true,
    speed: 0,
    charge_percent: 73,
    battery1_percent: 73,
    battery2_percent: 74,
    disk_percent: 17,
    mode: 'AUTO',
    icp_label: 'OFF',
    icp_ok: false,
    lte_label: 'LTE',
    lte_ok: null,
    connection: 'lte',
    error_banner: null,
    lat: 55,
    lon: 37,
    heading_deg: 80,
    wheels_fault: [],
    ...overrides,
  }
}

describe('robotHud', () => {
  it('maps online flag to active/offline labels', () => {
    expect(robotHudStatus(true)).toBe('active')
    expect(robotHudStatusLabel('active')).toBe(ru.emergency.statusActive)
    expect(robotHudStatus(false)).toBe('offline')
    expect(robotHudStatusLabel('offline')).toBe(ru.emergency.statusOffline)
    expect(robotHudStatus(null)).toBe('unknown')
  })

  it('prefers snapshot.connection and falls back to LTE flags', () => {
    expect(robotHudConnection(snapshot())).toBe('lte')
    expect(robotHudConnectionLabel('lte')).toBe(ru.emergency.connectionLte)
    expect(robotHudConnection(snapshot({ connection: 'wire' }))).toBe('wire')
    expect(robotHudConnectionLabel('wire')).toBe(ru.emergency.connectionWire)
    expect(
      robotHudConnection(snapshot({ connection: null, lte_ok: false, online: true })),
    ).toBe('wire')
    expect(
      robotHudConnection(snapshot({ connection: null, lte_ok: true, lte_label: null })),
    ).toBe('lte')
  })

  it('shows both packs when present and hides disconnected ones', () => {
    expect(robotHudBatteries(snapshot()).map((pack) => pack.label)).toEqual([
      ru.emergency.battery1,
      ru.emergency.battery2,
    ])
    expect(robotHudBatteries(snapshot({ battery2_percent: null }))).toEqual([
      { id: 1, percent: 73, label: ru.emergency.battery },
    ])
    expect(
      robotHudBatteries(
        snapshot({ battery1_percent: null, battery2_percent: null, charge_percent: 64 }),
      ),
    ).toEqual([{ id: 1, percent: 64, label: ru.emergency.battery }])
    expect(
      robotHudBatteries(
        snapshot({ battery1_percent: null, battery2_percent: null, charge_percent: null }),
      ),
    ).toEqual([])
  })

  it('formats telemetry and warns on low battery / full disk', () => {
    expect(formatHudSpeed(0)).toBe('0.0')
    expect(formatHudPercent(17.4)).toBe('17%')
    expect(metricTone('battery', 12)).toBe('warn')
    expect(metricTone('disk', 81)).toBe('warn')
    expect(metricTone('disk', 17)).toBe('ok')
  })

  it('puts a short label on the outer side of a broken wheel', () => {
    expect(wheelFaultSide('fl')).toBe('left')
    expect(wheelFaultSide('rr')).toBe('right')
    expect(wheelFaultLabel('fl')).toBe(ru.emergency.wheelSlotShort.fl)
    expect(wheelFaultLabel('rr')).toBe(ru.emergency.wheelSlotShort.rr)
    expect(wheelFaultLabel('body')).toBe(ru.emergency.wheelSlotShort.body)
  })
})
