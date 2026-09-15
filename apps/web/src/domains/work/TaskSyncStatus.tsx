import type { TaskSyncState } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'

const labels: Record<TaskSyncState, string> = {
  saved: 'Сохранено',
  synced: 'Сохранено',
  pending: 'Отправляется в Tracker',
  needs_attention: 'Требует внимания',
}

export function TaskSyncStatus({ state }: { state: TaskSyncState }) {
  const tone = state === 'needs_attention' ? 'critical' : state === 'pending' ? 'warning' : 'success'
  return <span role="status"><StatusBadge tone={tone}>{labels[state]}</StatusBadge></span>
}
