import { ApiError } from '../api'
import { ru } from './ru'

const opsErrors: Record<string, string> = {
  confirm_required: 'Введите фразу подтверждения точно, без лишних пробелов.',
  github_release_unavailable: 'Релиз больше недоступен. Список обновлений проверяется заново.',
  github_approval_actor_mismatch: 'Этот релиз уже подтверждён другим владельцем. Дождитесь завершения операции.',
  host_work_in_progress: 'Другая операция уже выполняется.',
  job_in_progress: 'Другая операция уже выполняется.',
  host_operation_failed: 'Хост не смог завершить операцию. Соберите диагностику для проверки.',
  host_bridge_unavailable: 'Служба управления хостом недоступна.',
  inspection_not_found: 'Проверка архива устарела. Выберите ZIP заново.',
  inspection_already_approved: 'Этот архив уже передан на установку.',
  artifact_missing: 'Архив ещё не готов или уже удалён.',
  artifact_changed: 'Архив изменился после проверки. Выберите ZIP заново.',
  archive_too_large: 'Архив превышает допустимый размер.',
  ops_job_conflict: 'Другая операция уже выполняется.',
  host_operation_not_abortable: 'Операция уже передана хосту и не может быть отменена.',
  release_not_available: 'Релиз больше недоступен. Список обновлений проверяется заново.',
  release_not_found: 'Релиз не найден. Список обновлений проверяется заново.',
  discovery_stale: 'Сведения об обновлениях устарели. Ожидаем проверку хоста.',
  updater_disabled: 'Проверка обновлений отключена на хосте.',
  invalid_confirmation: 'Введите фразу подтверждения точно, без лишних пробелов.',
  update_rolled_back: 'Обновление не прошло проверку. Восстановлена предыдущая версия.',
}

export function mapApiError(error: unknown, fallback = ''): string {
  if (error instanceof ApiError) {
    if (error.detail && opsErrors[error.detail]) return opsErrors[error.detail]
    if (error.detail && ru.errors.details[error.detail]) {
      return ru.errors.details[error.detail]
    }
    if (error.detail === 'maintenance' || (error.status === 503 && error.detail === 'maintenance')) {
      return ru.maintenance.title
    }
    if (error.status === 503) return ru.errors.tasks503
    if (error.status === 401) {
      return ru.errors.sessionExpired
    }
    if (error.status === 409) return ru.errors.tasks409
    if (error.status === 404) return ru.errors.notFound
    if (error.status === 502) return ru.errors.details.tracker_upstream_error
    // Never surface raw upstream/API detail strings in the UI.
  }
  if (error instanceof Error) {
    if (error.message === '403') return ru.errors.register403
    if (error.message === '409') return ru.errors.register409
  }
  return fallback
}

export function mapLoginError(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return ru.errors.login
  }
  return mapApiError(error, ru.errors.login)
}
