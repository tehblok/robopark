import { useEffect, useState } from 'react'
import { Button } from '../design-system/actions/Button'
import { useOptionalSync } from './syncContext'
import type { OfflineAction, OfflineMedia } from './offlineTypes'
import {
  activateServiceWorkerWhenSafe,
  serviceWorkerUpdateReady,
  subscribeServiceWorkerUpdate,
} from './registerServiceWorker'
import './SyncCenter.css'

export type SyncQueueItem = { id: string, label: string, state: 'pending' | 'conflict' | 'failed', cancelable?: boolean, reason?: string }
type StorageEstimate = { usage?: number, quota?: number }

function formatStorageBytes(value: number) {
  const gigabytes = value >= 1024 ** 3
  return `${(value / (gigabytes ? 1024 ** 3 : 1024 ** 2)).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} ${gigabytes ? 'ГБ' : 'МБ'}`
}

function measuredStorage(storage: StorageEstimate): storage is { usage: number, quota: number } {
  return typeof storage.usage === 'number' && Number.isFinite(storage.usage) && storage.usage >= 0
    && typeof storage.quota === 'number' && Number.isFinite(storage.quota) && storage.quota >= 0
}

const defaultEstimateStorage = () => navigator.storage?.estimate?.() ?? Promise.resolve({})

function actionLabel(item: OfflineAction): string {
  const names: Record<string, string> = {
    submit_review: 'Передать на проверку',
    comment: 'Комментарий',
    schedule_create: 'Создать период',
    schedule_update: 'Изменить период',
    schedule_delete: 'Удалить период',
    schedule_pattern: 'Применить шаблон',
    schedule_copy: 'Копировать график',
  }
  const subject = item.resourceType.startsWith('schedule') ? 'График' : item.resourceId
  return `${names[item.action] ?? 'Действие'} · ${subject}`
}

const actionFailureReasons: Record<string, string> = {
  offline_dependency_missing: 'Обязательная предыдущая запись недоступна. Отмените действие и создайте его заново.',
  dependency_missing: 'Обязательная предыдущая запись недоступна. Отмените действие и создайте его заново.',
  dependency_failed: 'Обязательная предыдущая запись недоступна. Отмените действие и создайте его заново.',
  park_forbidden: 'Доступ к парку изменился. Отмените действие и проверьте доступ перед повтором.',
  task_already_closed: 'Задача уже закрыта. Откройте её карточку и проверьте статус перед новым действием.',
  schedule_revision_conflict: 'График изменился на сервере. Откройте актуальный график перед повтором.',
}

function actionFailureReason(item: OfflineAction): string | undefined {
  const code = item.result?.code
  return (item.state === 'attention' || item.state === 'conflict') && typeof code === 'string'
    ? actionFailureReasons[code] : undefined
}

function queueItems(actions: OfflineAction[], media: OfflineMedia[]): SyncQueueItem[] {
  const uploadingMedia = new Set(media.filter(item => item.state === 'uploading').map(item => item.actionId))
  return [
    ...actions.filter(item => !['confirmed', 'cancelled'].includes(item.state)).map(item => ({
      id: item.id,
      label: actionLabel(item),
      state: item.state === 'conflict' ? 'conflict' as const : item.state === 'attention' ? 'failed' as const : 'pending' as const,
      reason: actionFailureReason(item),
      cancelable: item.state !== 'sending' && !uploadingMedia.has(item.id),
    })),
    ...media.filter(item => item.state !== 'confirmed').map(item => ({
      id: `media:${item.id}`,
      label: `Фото · ${item.issueKey}`,
      state: item.state === 'attention' ? 'failed' as const : 'pending' as const,
      cancelable: false,
    })),
  ]
}

function queueStateLabel(state: SyncQueueItem['state']): string {
  return state === 'conflict' ? 'Конфликт' : state === 'failed' ? 'Требует внимания' : 'Ожидает отправки'
}

export function SyncCenter({
  offlineSession = false,
  queue,
  estimateStorage = defaultEstimateStorage,
  updateReady: updateReadyOverride,
  activateUpdate,
}: {
  queue?: SyncQueueItem[]
  estimateStorage?: () => Promise<StorageEstimate>
  updateReady?: boolean
  activateUpdate?: () => boolean
  offlineSession?: boolean
}) {
  const sync = useOptionalSync()
  const [online, setOnline] = useState(() => navigator.onLine !== false)
  useEffect(() => {
    const changed = () => setOnline(navigator.onLine !== false)
    window.addEventListener('online', changed)
    window.addEventListener('offline', changed)
    return () => {
      window.removeEventListener('online', changed)
      window.removeEventListener('offline', changed)
    }
  }, [])
  const [open, setOpen] = useState(false)
  const [storage, setStorage] = useState<StorageEstimate>({})
  const [localQueue, setLocalQueue] = useState<{ scopeKey: string | null | undefined, items: SyncQueueItem[], error: boolean } | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [knownUpdate, setKnownUpdate] = useState(serviceWorkerUpdateReady)
  const updateReady = updateReadyOverride ?? knownUpdate

  useEffect(() => subscribeServiceWorkerUpdate(() => setKnownUpdate(serviceWorkerUpdateReady())), [])
  useEffect(() => {
    if (!open) return
    let active = true
    void estimateStorage().then(value => { if (active) setStorage(value) }).catch(() => undefined)
    return () => { active = false }
  }, [estimateStorage, open])
  useEffect(() => {
    if (!open || !sync || queue !== undefined) return
    let active = true
    const scopeKey = sync.scopeKey
    void Promise.all([sync.listActions?.() ?? Promise.resolve([]), sync.listMedia?.() ?? Promise.resolve([])])
      .then(([actions, media]) => {
        if (!active) return
        setLocalQueue({ scopeKey, items: queueItems(actions, media), error: false })
      })
      .catch(() => { if (active) setLocalQueue({ scopeKey, items: [], error: true }) })
    return () => { active = false }
  }, [open, queue, sync])

  if (!sync) return null
  const { state } = sync
  const offline = offlineSession || !online || state.status === 'offline'
  const label = offline ? 'Автосинхронизация: без сети'
    : state.status === 'syncing' ? 'Автосинхронизация: отправляем'
      : state.status === 'attention' || state.conflicts > 0 ? 'Автосинхронизация: нужно внимание'
        : state.pending > 0 ? 'Автосинхронизация: ожидает отправки' : 'Синхронизация выполняется автоматически'
  const visualState = offline ? 'offline'
    : state.status === 'syncing' ? 'syncing'
      : state.status === 'attention' || state.conflicts > 0 ? 'attention'
        : state.pending > 0 ? 'pending' : 'idle'
  const safeToUpdate = !offline && state.status === 'idle' && state.pending === 0 && state.conflicts === 0
  const scopedQueue = localQueue?.scopeKey === sync.scopeKey ? localQueue : null
  const displayedQueue = queue ?? scopedQueue?.items ?? []
  const canRetryQueue = displayedQueue.length === 0
    || displayedQueue.some(item => item.state === 'pending' || item.id.startsWith('media:'))

  return (
    <div className="rp-sync-center">
      <button
        aria-expanded={open}
        aria-label={`Открыть центр синхронизации. ${label}`}
        className="rp-sync-center__trigger"
        onClick={() => setOpen(value => !value)}
        type="button"
      >
        <span aria-hidden="true" className={`rp-sync-center__dot is-${visualState}`} />
        {state.pending > 0 ? <strong>{state.pending}</strong> : null}
      </button>
      {open ? (
        <section aria-label="Центр синхронизации" className="rp-sync-center__panel">
          <header><strong>Синхронизация</strong><button aria-label="Закрыть центр синхронизации" onClick={() => setOpen(false)} type="button">×</button></header>
          <div className="rp-sync-center__summary">
            <span>{state.conflicts > 0 ? 'Не завершено' : 'В очереди'}: {state.pending}</span>
            <span>Требуют решения: {state.conflicts}</span>
            <span>{measuredStorage(storage)
              ? `На устройстве: ${formatStorageBytes(storage.usage)} из ${formatStorageBytes(storage.quota)}`
              : 'Объём на устройстве не измерен'}</span>
          </div>
          {scopedQueue?.error ? <p role="alert">Не удалось прочитать локальную очередь.</p> : null}
          {actionError ? <p role="alert">{actionError}</p> : null}
          {displayedQueue.length ? <ul>{displayedQueue.slice(0, 20).map(item => <li key={item.id}><span>{item.label}<small>{queueStateLabel(item.state)}</small>{item.reason ? <small>{item.reason}</small> : null}</span>{item.cancelable !== false ? <button aria-label={`Отменить «${item.label}»`} onClick={() => void sync.cancelAction(item.id).then(() => setActionError(null)).catch(() => setActionError('Синхронизация занята или действие связано с другой записью. Повторите отмену позже.'))} type="button">Отменить</button> : null}</li>)}</ul> : null}
          {displayedQueue.length > 20 ? <p>Показаны первые 20 из {displayedQueue.length} действий.</p> : null}
          {updateReady ? safeToUpdate
            ? <Button onClick={() => (activateUpdate ?? (() => activateServiceWorkerWhenSafe(undefined, state)))()} size="compact" variant="secondary">Установить обновление</Button>
            : <p>Обновление будет доступно после отправки очереди.</p>
            : null}
          {state.status === 'attention' && state.pending > 0 && canRetryQueue
            ? <><p>После исправления причины повторите отправку. Конфликт задачи исправляется в её карточке.</p><Button onClick={() => void sync.syncNow('manual')} size="compact" variant="secondary">Повторить отправку</Button></>
            : state.status === 'attention' && state.pending > 0
              ? <p>Действие не будет отправлено повторно. Выберите решение в строке выше.</p>
            : <p>Отправка выполняется автоматически.</p>}
        </section>
      ) : null}
    </div>
  )
}
