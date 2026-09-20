import { useEffect, useState } from 'react'
import { api, type NotificationEvent } from '../api'
import { Button } from '../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../design-system/feedback/AsyncState'
import { Panel } from '../design-system/layout/PageLayout'

export type NotificationApiClient = {
  notificationInbox: () => Promise<NotificationEvent[]>
  notificationRead: (id: string) => Promise<{ ok: boolean }>
}
export function NotificationCenter({ apiClient = api }: { apiClient?: NotificationApiClient }) {
  const [items, setItems] = useState<NotificationEvent[] | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => { let active = true; void apiClient.notificationInbox().then(value => { if (active) setItems(value) }).catch(() => { if (active) setError(true) }); return () => { active = false } }, [apiClient])
  const markRead = async (id: string) => { await apiClient.notificationRead(id); setItems(current => current?.map(item => item.id === id ? { ...item, read_at: new Date().toISOString() } : item) ?? null) }
  if (error) return <ErrorState description="Уведомления остались на сервере. Повторите загрузку." title="Не удалось загрузить уведомления" />
  if (!items) return <LoadingState label="Загружаем уведомления" />
  if (items.length === 0) return <EmptyState title="Новых уведомлений нет" />
  return <Panel title="Уведомления" description="События сохраняются здесь независимо от разрешения системных уведомлений.">
    <ul className="rp-notification-list">{items.map(item => <li key={item.id}><div><strong>{item.event_type}</strong><p>{item.protected_text}</p></div>{!item.read_at ? <Button onClick={() => void markRead(item.id)} size="compact" variant="secondary">Прочитано</Button> : null}</li>)}</ul>
  </Panel>
}
