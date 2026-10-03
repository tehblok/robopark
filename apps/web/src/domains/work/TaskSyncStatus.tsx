import type { TaskSyncState } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'

const reasons: Record<string, string> = {
  task_already_closed: 'Тикет закрыт в Tracker. Отложенное действие сохранено в системе и не будет отправлено в новый цикл ремонта.',
  tracker_transition_missing: 'В Трекере нет доступного перехода в нужный статус. Администратору нужно проверить процесс очереди.',
  authentication: 'Проверьте токен бота и его доступ к Трекеру.',
  '401': 'Проверьте токен бота и его доступ к Трекеру.',
  '403': 'У бота недостаточно прав для этого действия в Трекере.',
  forbidden: 'У бота недостаточно прав для этого действия в Трекере.',
  invalid_payload: 'Трекер не принял данные действия. Обратитесь к администратору.',
  prerequisite_failed: 'Предыдущее действие не доставлено. Сначала нужно устранить его ошибку.',
  duplicate_remote_action: 'Найдено несколько совпадающих действий. Нужна проверка администратора.',
  tracker_operator_assignment_failed: 'Tracker отклонил назначение. Проверьте логин оператора в Tracker и права бота, затем повторите действие.',
  tracker_error: 'Tracker отклонил действие. Проверьте интеграцию и повторите после устранения причины.',
}

export function TaskSyncStatus({ state, errorCode }: { state: TaskSyncState; errorCode?: string | null }) {
  if (state !== 'needs_attention') return null
  return <span role="status"><StatusBadge tone="critical">Нужно внимание</StatusBadge>{errorCode && reasons[errorCode] ? <span> {reasons[errorCode]}</span> : null}</span>
}
