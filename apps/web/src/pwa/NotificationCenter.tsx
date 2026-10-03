import { useEffect, useRef, useState } from 'react'
import { api, type NotificationEvent } from '../api'
import { Button } from '../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../design-system/feedback/AsyncState'
import { Panel } from '../design-system/layout/PageLayout'
import { decodeApplicationServerKey, enableSystemNotifications } from './notifications'

export type NotificationApiClient = {
  notificationInbox: () => Promise<NotificationEvent[]>
  notificationRead: (id: string) => Promise<{ ok: boolean }>
  pushConfig?: () => Promise<{ public_key: string }>
  pushSubscribe?: (body: { endpoint: string; p256dh: string; auth: string }) => Promise<unknown>
}

const eventLabel: Record<string, string> = {
  new_task: 'Новая задача',
  return: 'Задача вернулась',
  operator_comment: 'Комментарий оператора',
  report: 'Новый репорт',
  review_task: 'Задача на проверку',
  problem: 'Проблема',
  anomaly: 'Аномалия',
  integration_down: 'Интеграция недоступна',
  disk_low: 'Мало места',
  update_failure: 'Сбой обновления',
  server_problem: 'Проблема системы',
}
export function NotificationCenter({ apiClient = api }: { apiClient?: NotificationApiClient }) {
  const [items, setItems] = useState<NotificationEvent[] | null>(null)
  const [error, setError] = useState(false)
  const [reload, setReload] = useState(0)
  const [readError, setReadError] = useState<string | null>(null)
  const [readingId, setReadingId] = useState<string | null>(null)
  const reading = useRef<string | null>(null)
  const [systemStatus, setSystemStatus] = useState<'idle' | 'busy' | 'enabled' | 'denied' | 'unsupported' | 'error'>('idle')
  useEffect(() => { let active = true; setError(false); void apiClient.notificationInbox().then(value => { if (active) { setItems(value); setError(false) } }).catch(() => { if (active) setError(true) }); return () => { active = false } }, [apiClient, reload])
  const markRead = async (id: string) => {
    if (reading.current) return
    reading.current = id
    setReadingId(id)
    setReadError(null)
    try {
      const result = await apiClient.notificationRead(id)
      if (!result.ok) throw new Error('notification_read_failed')
      setItems(current => current?.map(item => item.id === id ? { ...item, read_at: new Date().toISOString() } : item) ?? null)
    } catch {
      setReadError('Не удалось отметить уведомление прочитанным.')
    } finally {
      reading.current = null
      setReadingId(null)
    }
  }
  const enablePush = async () => {
    if (!apiClient.pushConfig || !apiClient.pushSubscribe || !('serviceWorker' in navigator)) {
      setSystemStatus('unsupported')
      return
    }
    setSystemStatus('busy')
    try {
      const [{ public_key }, registration] = await Promise.all([
        apiClient.pushConfig(), navigator.serviceWorker.ready,
      ])
      const result = await enableSystemNotifications(registration, decodeApplicationServerKey(public_key))
      if (result.status !== 'granted') {
        setSystemStatus(result.status)
        return
      }
      await apiClient.pushSubscribe(result)
      setSystemStatus('enabled')
    } catch {
      setSystemStatus('error')
    }
  }
  if (error) return <ErrorState description="Уведомления остались на сервере. Повторите загрузку." onRetry={() => setReload(current => current + 1)} title="Не удалось загрузить уведомления" />
  if (!items) return <LoadingState label="Загружаем уведомления" />
  return <Panel title="Уведомления" description="События сохраняются здесь независимо от разрешения системных уведомлений.">
    {readError ? <p role="alert">{readError}</p> : null}
    {systemStatus !== 'enabled' ? <div className="rp-notification-system">
      <Button disabled={systemStatus === 'busy'} onClick={() => void enablePush()} size="compact" variant="secondary">
        {systemStatus === 'busy' ? 'Включаем…' : 'Включить уведомления на устройстве'}
      </Button>
      {systemStatus === 'denied' ? <p>Разрешение отключено в настройках браузера.</p> : null}
      {systemStatus === 'unsupported' ? <p>Это устройство не поддерживает системные уведомления.</p> : null}
      {systemStatus === 'error' ? <p>Не удалось включить. Внутренние уведомления продолжат работать.</p> : null}
    </div> : <p>Уведомления на устройстве включены.</p>}
    {items.length === 0 ? <EmptyState title="Новых уведомлений нет" /> : <ul className="rp-notification-list">{items.map(item => <li key={item.id}><div><strong>{eventLabel[item.event_type] ?? 'Событие'}</strong><p>{item.protected_text}</p></div>{!item.read_at ? <Button busy={readingId === item.id} disabled={readingId !== null} onClick={() => void markRead(item.id)} size="compact" variant="secondary">Прочитано</Button> : null}</li>)}</ul>}
  </Panel>
}
