import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type User } from '../api'
import { Button } from '../design-system/actions/Button'
import { currentDeviceResourceCache } from '../lib/deviceResourceCache'
import { OFFLINE_PREPARATION_FRESH_MS, offlinePreparationKey, prepareOfflineWorkspace, type OfflinePreparationState } from './offlinePreparation'

type Props = {
  user: User
  loadState?: () => Promise<OfflinePreparationState | undefined>
  prepare?: (parkIds: number[], previous?: OfflinePreparationState) => Promise<OfflinePreparationState>
}

function count(value: number, one: string, few: string, many: string): string {
  const mod100 = value % 100
  const mod10 = value % 10
  const noun = mod100 >= 11 && mod100 <= 14 ? many : mod10 === 1 ? one : mod10 >= 2 && mod10 <= 4 ? few : many
  return `${value} ${noun}`
}

export function OfflineWorkspaceControl({ user, loadState, prepare }: Props) {
  const [state, setState] = useState<OfflinePreparationState | undefined>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  const [clock, setClock] = useState(() => Date.now())
  const generation = useRef(0)
  const load = useCallback(async () => loadState
    ? loadState()
    : currentDeviceResourceCache()?.get<OfflinePreparationState>(offlinePreparationKey(user.id)), [loadState, user.id])
  const runPreparation = useCallback(async (previous = state) => {
    if (busy || navigator.onLine === false) return
    const parkIds = user.parks.map(park => park.id)
    const operation = prepare ?? (async (ids: number[], prior?: OfflinePreparationState) => {
      const store = currentDeviceResourceCache()
      if (!store) throw new Error('offline_storage_unavailable')
      return prepareOfflineWorkspace({ user, selectedParkIds: ids, api, store, previous: prior })
    })
    const current = ++generation.current
    setBusy(true); setError(false)
    try {
      const result = await operation(parkIds, previous)
      if (current === generation.current) setState(result)
    } catch {
      if (current === generation.current) setError(true)
    } finally {
      if (current === generation.current) setBusy(false)
    }
  }, [busy, prepare, state, user])

  useEffect(() => {
    let active = true
    const current = generation.current
    void load().then(value => { if (active && current === generation.current) setState(value) }).catch(() => undefined)
    return () => { active = false }
  }, [load])
  useEffect(() => {
    const reconnect = () => { if (state) void runPreparation(state) }
    window.addEventListener('online', reconnect)
    return () => window.removeEventListener('online', reconnect)
  }, [runPreparation, state])
  useEffect(() => {
    if (!state) return
    const remaining = OFFLINE_PREPARATION_FRESH_MS - (Date.now() - state.preparedAt)
    if (remaining <= 0) return
    const timer = window.setTimeout(() => setClock(Date.now()), remaining)
    return () => window.clearTimeout(timer)
  }, [state])

  const prepared = state ? `${count(state.parkCount, 'парк', 'парка', 'парков')} · ${count(state.taskCount, 'задача', 'задачи', 'задач')}` : null
  const stale = state?.state === 'stale' || Boolean(state && clock - state.preparedAt >= OFFLINE_PREPARATION_FRESH_MS)
  return (
    <section aria-label="Офлайн-работа" className="rp-sync-center__offline">
      <strong>Офлайн-работа</strong>
      {prepared ? <p>{prepared}</p> : <p>Данные для работы без сети ещё не подготовлены.</p>}
      {state ? <p>{stale ? `Сохранённые данные устарели (от ${new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(state.preparedAt))}). Подключитесь к сети для обновления.` : `Обновлено: ${new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(state.preparedAt))}`}{state.complete ? '' : ' · Достигнут лимит; показана первая часть.'}</p> : null}
      {error ? <p role="alert">Не удалось обновить офлайн-набор. Сохранённые данные и очередь изменений оставлены без изменений.</p> : null}
      <Button disabled={busy || navigator.onLine === false || user.parks.length === 0} onClick={() => void runPreparation()} size="compact" variant="secondary">
        {busy ? 'Подготавливаем…' : state ? 'Обновить офлайн' : 'Подготовить офлайн'}
      </Button>
      <p>Офлайн поддерживаются подготовленные списки и уже доступные формы с очередью синхронизации. Терминал, администрирование и свежая телеметрия доступны только с сетью.</p>
    </section>
  )
}
