import { useEffect } from 'react'
import type { OpsJob } from '../../api'
import { useCachedResource } from '../../lib/resource'

const UPDATE_PHASE_LABELS: Record<string, string> = {
  validating: 'Проверяем архив',
  verified: 'Архив проверен',
  unpacking: 'Распаковываем обновление',
  unpacked: 'Обновление распаковано',
  building: 'Собираем API и веб-интерфейс',
  built: 'Сборка завершена',
  testing: 'Запускаем тесты',
  tested: 'Тесты завершены',
  smoking: 'Проверяем новую версию',
  smoked: 'Новая версия прошла проверку',
  maintenance: 'Включаем режим техработ',
  stopping: 'Останавливаем текущую версию',
  snapshotting: 'Создаём резервную копию',
  snapshotted: 'Резервная копия создана',
  tools_staging: 'Готовим служебные файлы',
  tools_staged: 'Служебные файлы готовы',
  publishing: 'Публикуем версию',
  published: 'Версия опубликована',
  switching: 'Переключаем версию',
  switched: 'Версия переключена',
  migrating: 'Обновляем данные',
  migrated: 'Данные обновлены',
  starting: 'Запускаем сервисы',
  started: 'Сервисы запущены',
  activating: 'Активируем версию',
  activated: 'Версия активирована',
  health_check: 'Проверяем работоспособность',
  healthy: 'Новая версия работает',
  reconciling: 'Завершаем обновление',
  publication: 'Проверяем публикацию',
  publication_checked: 'Публикация проверена',
  resuming: 'Возобновляем работу',
  succeeded: 'Обновление завершено',
  rolling_back: 'Восстанавливаем предыдущую версию',
  rollback_healthy: 'Предыдущая версия работает',
  rollback_resuming: 'Возобновляем работу после отката',
  rolled_back: 'Предыдущая версия восстановлена',
  failed: 'Обновление не завершено',
  manual_recovery_required: 'Требуется ручное восстановление',
}

const ROLLBACK_PHASES = new Set([
  'rolling_back', 'rollback_healthy', 'rollback_resuming', 'rolled_back',
])

export function updateProgress(job: OpsJob) {
  const phase = job.progress_phase
  const percent = job.progress_percent
  if (
    job.kind !== 'update'
    || typeof phase !== 'string'
    || UPDATE_PHASE_LABELS[phase] == null
    || typeof percent !== 'number'
    || !Number.isInteger(percent)
    || percent < 0
    || percent > 100
  ) return null
  return { percent, label: UPDATE_PHASE_LABELS[phase], rollback: ROLLBACK_PHASES.has(phase) }
}

export function useOpsResource<T>(key: string, loader: () => Promise<T>) {
  const resource = useCachedResource(key, loader, { persist: false })
  const { refresh } = resource
  useEffect(() => {
    const focus = () => { if (!document.hidden) void refresh() }
    window.addEventListener('focus', focus)
    document.addEventListener('visibilitychange', focus)
    return () => { window.removeEventListener('focus', focus); document.removeEventListener('visibilitychange', focus) }
  }, [refresh])
  return resource
}

// Server messages are already projected; suppress accidental technical payloads
// from old hosts as well. Never render raw job logs or unknown error details.
export function opsText(value: string | null | undefined, fallback = 'Нет данных') {
  if (!value || /(?:traceback|exception|https?:\/\/|[{}]|(?:^|\s)\/[\w.]|\\|(?:token|password|secret|cookie)\s*[:=])/i.test(value)) return fallback
  return value.slice(0, 1000)
}

export function age(value: string | null) {
  if (!value || !Number.isFinite(Date.parse(value))) return 'нет данных'
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(value)) / 60000))
  if (minutes < 1) return 'только что'
  if (minutes < 60) return `${minutes} мин. назад`
  if (minutes < 1440) return `${Math.floor(minutes / 60)} ч. назад`
  return `${Math.floor(minutes / 1440)} дн. назад`
}

export function staleHealth(value: string | null | undefined) {
  return !value || !Number.isFinite(Date.parse(value)) || Date.now() - Date.parse(value) > 5 * 60000
}

export const repairLabels: Record<string, string> = {
  restart_docker: 'перезапуск Docker', restart_app: 'перезапуск приложения', restart_tuna: 'перезапуск Tuna', daemon_reload: 'обновление служб',
}
