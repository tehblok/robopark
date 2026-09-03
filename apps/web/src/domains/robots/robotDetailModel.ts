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

export function buildRobotDetailModel(snapshot: EmergencySnapshot, browserOnline: boolean, now = new Date()): RobotDetailViewModel {
  const age = Math.max(0, now.getTime() - new Date(snapshot.observed_at).getTime())
  const freshness: Freshness = !browserOnline ? 'offline' : age <= 30_000 ? 'live' : age <= 300_000 ? 'fresh' : 'stale'
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
