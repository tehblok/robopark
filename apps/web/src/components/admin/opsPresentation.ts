import { useEffect } from 'react'
import { useCachedResource } from '../../lib/resource'

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
