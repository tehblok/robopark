import type { EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'

export type RobotHudStatus = 'active' | 'offline' | 'unknown'
export type RobotHudConnection = 'lte' | 'wire' | null

export type BatteryTile = {
  id: 1 | 2
  percent: number
  label: string
}

function isPercent(value: number | null | undefined): value is number {
  return value != null && !Number.isNaN(value)
}

export function robotHudStatus(online: boolean | null | undefined): RobotHudStatus {
  if (online === true) return 'active'
  if (online === false) return 'offline'
  return 'unknown'
}

export function robotHudStatusLabel(status: RobotHudStatus): string {
  if (status === 'active') return ru.emergency.statusActive
  if (status === 'offline') return ru.emergency.statusOffline
  return '—'
}

export function robotHudConnection(snapshot: EmergencySnapshot | null | undefined): RobotHudConnection {
  const raw = snapshot?.connection
  if (raw === 'lte' || raw === 'wire') return raw
  if (snapshot?.lte_ok === true) return 'lte'
  if (snapshot?.lte_ok === false) {
    return snapshot.online === true ? 'wire' : null
  }
  if (snapshot?.lte_label && /lte/i.test(snapshot.lte_label)) return 'lte'
  return null
}

export function robotHudConnectionLabel(connection: RobotHudConnection): string {
  if (connection === 'lte') return ru.emergency.connectionLte
  if (connection === 'wire') return ru.emergency.connectionWire
  return '—'
}

export function robotHudBatteries(snapshot: EmergencySnapshot | null | undefined): BatteryTile[] {
  if (!snapshot) return []
  const packs: Array<{ id: 1 | 2; percent: number }> = []
  if (isPercent(snapshot.battery1_percent)) {
    packs.push({ id: 1, percent: snapshot.battery1_percent })
  }
  if (isPercent(snapshot.battery2_percent)) {
    packs.push({ id: 2, percent: snapshot.battery2_percent })
  }
  if (packs.length === 0 && isPercent(snapshot.charge_percent)) {
    packs.push({ id: 1, percent: snapshot.charge_percent })
  }
  const named = packs.length > 1
  return packs.map((pack) => ({
    ...pack,
    label: named
      ? pack.id === 1
        ? ru.emergency.battery1
        : ru.emergency.battery2
      : ru.emergency.battery,
  }))
}

export function formatHudSpeed(speed: number | null | undefined): string {
  if (speed == null || Number.isNaN(speed)) return '—'
  return speed.toFixed(1)
}

export function formatHudPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  return `${Math.round(value)}%`
}

export function metricTone(
  kind: 'battery' | 'disk',
  value: number | null | undefined,
): 'ok' | 'warn' | 'muted' {
  if (value == null || Number.isNaN(value)) return 'muted'
  if (kind === 'battery') return value < 20 ? 'warn' : 'ok'
  return value >= 80 ? 'warn' : 'ok'
}

export function wheelFaultSide(slot: string): 'left' | 'right' {
  return slot.endsWith('l') ? 'left' : 'right'
}

export function wheelFaultLabel(slot: string): string {
  const labels = ru.emergency.wheelSlotShort as Record<string, string>
  return labels[slot] ?? ru.emergency.wheelSlots[slot] ?? slot
}
