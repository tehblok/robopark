import type { EmergencySnapshot } from '../../api'
import type { Freshness } from '../../design-system/feedback/AsyncState'
import type { StatusTone } from '../../design-system/status/StatusBadge'

export type RobotConnectionState = 'device-offline' | 'robot-offline' | 'online' | 'unknown'
export type RobotDetailViewModel = {
  vin: string
  shortNumber: string
  observedAt: string
  freshness: Freshness
  connection: { state: RobotConnectionState; tone: StatusTone; label: string }
  criticalReason: string | null
}

export const ROBOT_OBSERVATION_FUTURE_TOLERANCE_MS = 60_000

const ROBOT_MODES: Record<string, string> = {
  AUTO: 'Автономный',
  MANUAL: 'Ручной',
  PAUSE: 'Пауза',
  IDLE: 'Ожидание',
}

export function formatRobotMode(value: string | null | undefined): string {
  const mode = value?.trim()
  if (!mode) return 'Нет данных'
  return ROBOT_MODES[mode.toUpperCase()] ?? mode
}

export function buildRobotDetailModel(snapshot: EmergencySnapshot, browserOnline: boolean, now = new Date()): RobotDetailViewModel {
  const age = now.getTime() - Date.parse(snapshot.observed_at)
  const plausibleTime = Number.isFinite(age) && age >= -ROBOT_OBSERVATION_FUTURE_TOLERANCE_MS
  const freshness: Freshness = !browserOnline ? 'offline' : !plausibleTime ? 'stale' : age <= 30_000 ? 'live' : age <= 300_000 ? 'fresh' : 'stale'
  const connection: RobotDetailViewModel['connection'] = !browserOnline
    ? { state: 'device-offline', tone: 'warning', label: 'Нет сети на этом устройстве' }
    : snapshot.online === false
      ? { state: 'robot-offline', tone: 'critical', label: 'Робот не в сети' }
      : snapshot.online === true
        ? { state: 'online', tone: 'success', label: 'Робот на связи' }
        : { state: 'unknown', tone: 'neutral', label: 'Связь не определена' }
  return {
    vin: snapshot.vin, shortNumber: snapshot.short_number, observedAt: snapshot.observed_at,
    freshness, connection,
    criticalReason: snapshot.error_banner || (snapshot.wheels_fault.length ? 'Обнаружена неисправность колёс' : null),
  }
}
