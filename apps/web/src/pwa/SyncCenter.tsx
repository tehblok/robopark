import { useEffect, useState } from 'react'
import { Button } from '../design-system/actions/Button'
import { useOptionalSync } from './SyncProvider'
import {
  activateServiceWorkerWhenSafe,
  serviceWorkerUpdateReady,
  subscribeServiceWorkerUpdate,
} from './registerServiceWorker'
import './SyncCenter.css'

export type SyncQueueItem = { id: string, label: string, state: 'pending' | 'conflict' | 'failed' }
type StorageEstimate = { usage?: number, quota?: number }

function megabytes(value = 0) {
  return `${(value / (1024 * 1024)).toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} МБ`
}

const defaultEstimateStorage = () => navigator.storage?.estimate?.() ?? Promise.resolve({})

export function SyncCenter({
  queue = [],
  estimateStorage = defaultEstimateStorage,
  updateReady: updateReadyOverride,
  activateUpdate,
}: {
  queue?: SyncQueueItem[]
  estimateStorage?: () => Promise<StorageEstimate>
  updateReady?: boolean
  activateUpdate?: () => boolean
}) {
  const sync = useOptionalSync()
  const [open, setOpen] = useState(false)
  const [storage, setStorage] = useState<StorageEstimate>({})
  const [knownUpdate, setKnownUpdate] = useState(serviceWorkerUpdateReady)
  const updateReady = updateReadyOverride ?? knownUpdate

  useEffect(() => subscribeServiceWorkerUpdate(() => setKnownUpdate(serviceWorkerUpdateReady())), [])
  useEffect(() => {
    if (!open) return
    let active = true
    void estimateStorage().then(value => { if (active) setStorage(value) }).catch(() => undefined)
    return () => { active = false }
  }, [estimateStorage, open])

  if (!sync) return null
  const { state } = sync
  const label = state.status === 'offline' ? 'Автосинхронизация: без сети'
    : state.status === 'syncing' ? 'Автосинхронизация: отправляем'
      : state.status === 'attention' || state.conflicts > 0 ? 'Автосинхронизация: нужно внимание'
        : state.pending > 0 ? 'Автосинхронизация: ожидает отправки' : 'Синхронизация выполняется автоматически'
  const visualState = state.status === 'offline' ? 'offline'
    : state.status === 'syncing' ? 'syncing'
      : state.status === 'attention' || state.conflicts > 0 ? 'attention'
        : state.pending > 0 ? 'pending' : 'idle'
  const safeToUpdate = state.status === 'idle' && state.pending === 0 && state.conflicts === 0

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
        <span>{label}</span>
        {state.pending > 0 ? <strong>{state.pending}</strong> : null}
      </button>
      {open ? (
        <section aria-label="Центр синхронизации" className="rp-sync-center__panel">
          <header><strong>Синхронизация</strong><button aria-label="Закрыть центр синхронизации" onClick={() => setOpen(false)} type="button">×</button></header>
          <div className="rp-sync-center__summary">
            <span>В очереди: {state.pending}</span>
            <span>Требуют решения: {state.conflicts}</span>
            <span>На устройстве: {megabytes(storage.usage)} из {megabytes(storage.quota)}</span>
          </div>
          {queue.length ? <ul>{queue.map(item => <li key={item.id}><span>{item.label}</span><button aria-label={`Отменить «${item.label}»`} onClick={() => void sync.cancelAction(item.id)} type="button">Отменить</button></li>)}</ul> : null}
          {updateReady ? safeToUpdate
            ? <Button onClick={() => (activateUpdate ?? (() => activateServiceWorkerWhenSafe(undefined, state)))()} size="compact" variant="secondary">Установить обновление</Button>
            : <p>Обновление будет доступно после отправки очереди.</p>
            : null}
          <p>Отправка выполняется автоматически.</p>
        </section>
      ) : null}
    </div>
  )
}
