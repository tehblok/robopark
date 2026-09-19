import type { TaskSyncState } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'

const labels: Record<TaskSyncState, string> = {
  saved: '',
  synced: '',
  pending: 'Отправляется в Tracker',
  needs_attention: 'Требует внимания',
}

const reasons: Record<string, string> = {
  task_already_closed: 'Задача уже закрыта в Трекере. Отложенная смена статуса остановлена.',
  tracker_transition_missing: 'В Трекере нет доступного перехода в нужный статус. Администратору нужно проверить процесс очереди.',
  authentication: 'Проверьте токен бота и его доступ к Трекеру.',
  '401': 'Проверьте токен бота и его доступ к Трекеру.',
  '403': 'У бота недостаточно прав для этого действия в Трекере.',
  forbidden: 'У бота недостаточно прав для этого действия в Трекере.',
  invalid_payload: 'Трекер не принял данные действия. Обратитесь к администратору.',
  prerequisite_failed: 'Предыдущее действие не доставлено. Сначала нужно устранить его ошибку.',
  duplicate_remote_action: 'Найдено несколько совпадающих действий. Нужна проверка администратора.',
}

export function TaskSyncStatus({ state, errorCode }: { state: TaskSyncState; errorCode?: string | null }) {
  if (state === 'saved' || state === 'synced') return null
  const tone = state === 'needs_attention' ? 'critical' : state === 'pending' ? 'warning' : 'success'
  return <span role="status"><StatusBadge tone={tone}>{labels[state]}</StatusBadge>{state === 'needs_attention' && errorCode && reasons[errorCode] ? <span> {reasons[errorCode]}</span> : null}</span>
}
