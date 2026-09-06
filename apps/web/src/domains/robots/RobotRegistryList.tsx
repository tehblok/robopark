import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, type RobotRegistry } from '../../api'
import { useAuth } from '../../auth-context'
import { Button } from '../../design-system/actions/Button'
import { EntityRow } from '../../design-system/data/EntityRow'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { Panel } from '../../design-system/layout/PageLayout'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { checkAccessIdentity } from './robotCheckUrl'

function RegistryOwner({ apiClient, parkId, scopeLoading }: { apiClient: Partial<Pick<typeof api, 'robotRegistry'>>; parkId: number | null; scopeLoading: boolean }) {
  const { user, refreshUser } = useAuth()
  const [params, setParams] = useSearchParams()
  // ParkScopeProvider settles its URL selection after render. Do not treat that
  // initial null (or the previous park during navigation) as a selected scope.
  const scopePending = scopeLoading || (params.has('park') && params.get('park') !== String(parkId))
  const query = params.get('query') ?? ''
  const state = ['online', 'offline', 'unknown'].includes(params.get('state') ?? '') ? params.get('state')! : 'all'
  const activeErrors = params.get('active_errors') === 'true'
  const openTasks = params.get('open_tasks') === 'true'
  const offset = Math.max(0, Number(params.get('offset')) || 0)
  const [data, setData] = useState<RobotRegistry | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [resultPark, setResultPark] = useState(parkId)
  const [retry, setRetry] = useState(0)
  const lastRetry = useRef(0)
  const cache = useRef(new Map<string, { data: RobotRegistry; updatedAt: number }>())
  const requests = useRef(new Map<string, Promise<RobotRegistry>>())
  const settledPark = useRef<number | null | undefined>(scopePending ? undefined : parkId)
  const updateParams = useRef(setParams)
  updateParams.current = setParams
  const denied = useRef<{ kind: string; parkId: number | null } | null>(null)
  const refresh = useRef(refreshUser)
  refresh.current = refreshUser
  const allowed = Boolean(user?.permissions?.includes('tracker.read'))
  useEffect(() => {
    let current = true
    if (scopePending) return
    const replaceOffset = (nextOffset: number) => updateParams.current(previous => {
      const next = new URLSearchParams(previous)
      if (nextOffset) next.set('offset', String(nextOffset)); else next.delete('offset')
      return next
    }, { replace: true })
    const scopeChanged = settledPark.current !== undefined && settledPark.current !== parkId
    settledPark.current = parkId
    if (scopeChanged && offset > 0) {
      replaceOffset(0)
      return
    }
    if (denied.current?.kind === 'unauthorized' || (denied.current && denied.current.parkId === parkId)) return
    denied.current = null
    setData(null); setError(null)
    setResultPark(parkId)
    if (!allowed || !apiClient.robotRegistry) return
    const key = JSON.stringify([parkId, query, state, activeErrors, openTasks, offset])
    if (retry !== lastRetry.current) { cache.current.delete(key); lastRetry.current = retry }
    let timer: number | undefined
    const automatic = () => current && !document.hidden && navigator.onLine && !denied.current
    const clear = () => window.clearTimeout(timer)
    const publish = (value: RobotRegistry) => {
      if (!current) return
      if (offset > 0 && offset >= value.total) {
        replaceOffset(Math.max(0, Math.floor((value.total - 1) / 50) * 50))
        return
      }
      setData(value); setError(null)
    }
    const load = async () => {
      clear()
      if (!automatic()) return
      const cached = cache.current.get(key)
      try {
        if (cached && Date.now() - cached.updatedAt < 30_000) publish(cached.data)
        else {
          let pending = requests.current.get(key)
          if (!pending) {
            pending = apiClient.robotRegistry!({ park_id: parkId ?? undefined, query, state, active_errors: activeErrors, open_tasks: openTasks, offset, limit: 50 }).then(value => {
              if (cache.current.size >= 50) cache.current.delete(cache.current.keys().next().value!)
              cache.current.set(key, { data: value, updatedAt: Date.now() })
              return value
            }).finally(() => { requests.current.delete(key) })
            requests.current.set(key, pending)
          }
          publish(await pending)
        }
      } catch (failure) {
        if (!current) return
        setError(failure)
        const kind = classifyApiError(failure, 'Не удалось загрузить реестр.').kind
        if (kind === 'unauthorized' || kind === 'forbidden') {
          denied.current = { kind, parkId }; cache.current.clear(); setData(null)
        }
        if (kind === 'unauthorized') void refresh.current().catch(() => undefined)
      } finally {
        clear()
        if (automatic()) timer = window.setTimeout(() => void load(), 30_000)
      }
    }
    const resume = () => { clear(); if (automatic()) void load() }
    const cached = cache.current.get(key)
    if (cached) publish(cached.data)
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('online', resume)
    window.addEventListener('offline', clear)
    void load()
    return () => {
      current = false; clear()
      document.removeEventListener('visibilitychange', resume)
      window.removeEventListener('online', resume)
      window.removeEventListener('offline', clear)
    }
  }, [apiClient, parkId, scopePending, query, state, activeErrors, openTasks, offset, retry, allowed])
  const change = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value); else next.delete(key)
    if (key !== 'offset') next.delete('offset')
    setParams(next, { replace: true })
  }
  const visible = resultPark === parkId && !scopePending
  const failure = error && (visible || denied.current?.kind === 'unauthorized') ? classifyApiError(error, 'Не удалось загрузить реестр роботов.') : null
  const href = (vin: string, tab?: string) => {
    const next = new URLSearchParams()
    if (parkId != null) next.set('park', String(parkId))
    if (tab) next.set('tab', tab)
    return `/robots/${encodeURIComponent(vin)}${next.size ? `?${next}` : ''}`
  }
  return <Panel className="rp-robot-registry" title="Роботы в работе" description="Роботы с задачами в доступных парках. Список не подтверждает полный состав парка.">
    <div className="rp-robot-registry__filters">
      <label>Поиск по номеру, VIN или задаче<input type="search" value={query} placeholder="447, VIN или ROBOPARK-42" onChange={event => change('query', event.target.value)} /></label>
      <label>Доступность<select value={state} onChange={event => change('state', event.target.value === 'all' ? '' : event.target.value)}>
        <option value="all">Любая</option><option value="online">На связи</option><option value="offline">Не в сети</option><option value="unknown">Нет данных</option>
      </select></label>
      <label className="rp-robot-registry__check"><input type="checkbox" checked={activeErrors} onChange={event => change('active_errors', event.target.checked ? 'true' : '')} />Активные ошибки</label>
      <label className="rp-robot-registry__check"><input type="checkbox" checked={openTasks} onChange={event => change('open_tasks', event.target.checked ? 'true' : '')} />Открытые задачи</label>
    </div>
    {!allowed ? <EmptyState title="Реестр недоступен" description="Для реестра нужен доступ к задачам Tracker. Можно открыть робота по номеру ниже." />
      : failure ? <ErrorState {...failure} onRetry={failure.retryable ? () => setRetry(value => value + 1) : undefined} />
        : !apiClient.robotRegistry ? null : !visible || !data ? <LoadingState label="Загружаем реестр роботов" /> : <>
          <div className="rp-robot-registry__summary"><p>Найдено: {data.total}</p></div>
          <p className="rp-robot-registry__note" role="status">Телеметрия получена только из недавних проверок. {data.partial ? 'Часть данных неизвестна; откройте робота для проверки.' : 'Доступны сохранённые данные.'}</p>
          {data.items.length ? <div className="rp-robot-registry__rows" role="list" aria-label="Реестр роботов">
            <div className="rp-robot-registry__columns" aria-hidden="true"><span>Робот / VIN</span><span>Доступность · заряд · ошибки</span><span>Действия</span></div>
            {data.items.map(row => <div role="listitem" key={row.vin}><EntityRow
              title={<Link aria-label={`Открыть робота ${row.short_number}`} to={href(row.vin)}>Робот {row.short_number}</Link>}
              meta={row.vin} statusLabel={`Состояние робота ${row.short_number}`}
              status={<><span data-state={row.state}>{row.state === 'online' ? 'На связи' : row.state === 'offline' ? 'Не в сети' : 'Нет данных'}</span><span>Заряд: {row.telemetry?.charge_percent == null ? '—' : `${row.telemetry.charge_percent} %`}</span><span>Ошибки: {row.error_count ?? '—'}</span></>}
              actions={<Link to={href(row.vin, 'tasks')} aria-label={`Задачи робота ${row.short_number}`}>Задачи: {row.task_count}</Link>} /></div>)}
          </div> : <EmptyState title="Роботы не найдены" description="Измените поиск или фильтры. Можно открыть робота по номеру ниже." />}
          <nav aria-label="Страницы реестра" className="rp-robot-registry__pagination"><Button variant="secondary" disabled={offset === 0} onClick={() => change('offset', String(Math.max(0, offset - 50)))}>Назад</Button><span>{data.total ? `${data.offset + 1}–${data.offset + data.items.length} из ${data.total}` : '0 роботов'}</span><Button variant="secondary" disabled={!data.has_more} onClick={() => change('offset', String(offset + 50))}>Далее</Button></nav>
        </>}
  </Panel>
}

export function RobotRegistryList(props: React.ComponentProps<typeof RegistryOwner>) {
  const { user } = useAuth()
  return <RegistryOwner key={user ? `${user.id}:${checkAccessIdentity(user)}` : 'anonymous'} {...props} />
}
