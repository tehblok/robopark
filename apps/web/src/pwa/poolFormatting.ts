import type { AgentPoolStatus as PoolStatus } from '../api'

export const statusLabel: Record<PoolStatus['devices'][number]['status'], string> = {
  online: 'в сети', ready: 'готов', busy: 'занят', corporate_unavailable: 'корпоративная сеть недоступна', paused: 'приостановлен', stale: 'нет свежих данных', offline: 'не в сети',
}

export function formatBytes(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} МБ`
  return `${Math.round(value / 1_000)} КБ`
}

export type GraphDevice = Pick<PoolStatus['devices'][number], 'id' | 'label' | 'status' | 'owner_user_id' | 'owner_username' | 'owner_display_name'>

export function ownerName(device: GraphDevice): string {
  return device.owner_display_name || device.owner_username || 'Сотрудник'
}
