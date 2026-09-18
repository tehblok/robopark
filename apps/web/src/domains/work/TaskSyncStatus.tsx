import type { TaskSyncState } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'

const labels: Record<TaskSyncState, string> = {
  saved: '',
  synced: '',
  pending: 'Отправляется в Tracker',
  needs_attention: 'Требует внимания',
}

export function TaskSyncStatus({ state }: { state: TaskSyncState }) {
  if (state === 'saved' || state === 'synced') return null
  const tone = state === 'needs_attention' ? 'critical' : state === 'pending' ? 'warning' : 'success'
  return <span role="status"><StatusBadge tone={tone}>{labels[state]}</StatusBadge></span>
}
