import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type InventoryOverview } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryInt64Compare, isPositiveInventoryQuantity } from './inventoryTypes'
import type { OfflineActionInput } from '../../pwa/syncEngine'
import type { OfflineAction } from '../../pwa/offlineTypes'
import { buildInventoryWriteoffAction } from '../work/offlineTaskActions'
import './inventory.css'

type TaskPartsApi = Pick<typeof api, 'inventory' | 'writeoffInventoryForTask' | 'inventoryComponentPhotoUrl' | 'inventoryPartPhotoUrl'> & {
  searchInventory?: typeof api.searchInventory
}

type TaskPartsProps = {
  parkId: number | null
  issueKey: string
  apiClient?: TaskPartsApi
  onWritten?: (receipt: string) => void
  enqueueAction?: (input: OfflineActionInput) => Promise<unknown>
  queuedAction?: OfflineAction | null
  onQueued?: (action: OfflineAction) => void
  hydratingAction?: boolean
  actionHydrationError?: boolean
  onRetryActionHydration?: () => void
  cancelQueuedAction?: (id: string) => Promise<void>
}

export function TaskPartsPanel(props: TaskPartsProps) {
  return <TaskPartsContent key={`${props.parkId}:${props.issueKey}`} {...props} />
}

function TaskPartsContent({ parkId, issueKey, apiClient = api, onWritten, enqueueAction, queuedAction, onQueued, hydratingAction = false, actionHydrationError = false, onRetryActionHydration, cancelQueuedAction }: TaskPartsProps) {
  const [data, setData] = useState<InventoryOverview | null>(null)
  const [componentId, setComponentId] = useState(0)
  const [partId, setPartId] = useState(0)
  const [quantity, setQuantity] = useState('1')
  const [error, setError] = useState<unknown>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [localQueuedAction, setLocalQueuedAction] = useState<OfflineAction | null>(null)
  const [receipt, setReceipt] = useState('')
  const [searchDraft, setSearchDraft] = useState('')
  const [query, setQuery] = useState('')
  const submitting = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const [globalCatalog, setGlobalCatalog] = useState(false)
  const [total, setTotal] = useState(0)
  const [nextOffset, setNextOffset] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const loadGeneration = useRef(0)
  const idempotencyKey = useRef<string | null>(null)
  const controlledQueue = queuedAction !== undefined
  const activeQueuedAction = controlledQueue ? queuedAction : localQueuedAction
  const previousControlledActionId = useRef<string | null>(null)
  useEffect(() => {
    if (!controlledQueue) return
    if (queuedAction) {
      previousControlledActionId.current = queuedAction.id
      return
    }
    if (previousControlledActionId.current) {
      previousControlledActionId.current = null
      idempotencyKey.current = null
      setLocalQueuedAction(null)
      setReceipt('')
    }
  }, [controlledQueue, queuedAction])
  const pending = Boolean(activeQueuedAction && ['local', 'ready', 'sending'].includes(activeQueuedAction.state))
  const queueFailed = Boolean(activeQueuedAction && ['conflict', 'attention'].includes(activeQueuedAction.state))
  const load = useCallback(async (offset = 0, append = false) => {
    if (parkId == null) return false
    const generation = ++loadGeneration.current
    if (append) setLoadingMore(true)
    try {
      if (apiClient.searchInventory && (apiClient === api || apiClient.searchInventory !== api.searchInventory)) {
        const result = await apiClient.searchInventory({ parkId, query, limit: 200, offset })
        if (generation !== loadGeneration.current) return
        setData(current => {
          const previous = append && current?.park_id === parkId ? current.components.flatMap(component => component.parts) : []
          const usefulItems = result.items.filter(item => item.is_active)
          const page = usefulItems.map(item => ({
            id: item.id,
            park_id: parkId,
            component_id: item.component_id,
            name: item.name,
            article: item.article,
            quantity: item.stock_is_active ? item.quantity : '0' as const,
            minimum_quantity: item.minimum_quantity,
            location: item.location ?? 'Не указано',
            is_active: item.is_active && item.stock_is_active,
            has_photo: item.has_photo,
          }))
          const parts = Array.from(new Map([...previous, ...page].map(part => [part.id, part])).values())
          const componentNames = new Map<number, string>()
          if (append && current) current.components.forEach(component => componentNames.set(component.id, component.name))
          usefulItems.forEach(item => componentNames.set(item.component_id, item.component_name))
          const components = Array.from(componentNames.entries()).map(([id, name]) => ({ id, park_id: parkId, name, has_photo: false, parts: parts.filter(part => part.component_id === id) }))
          return { park_id: parkId, component_count: components.length, part_count: result.total, low_stock_count: 0, out_of_stock_count: 0, components }
        })
        setGlobalCatalog(true)
        setTotal(result.total)
        setNextOffset(result.offset + result.items.length)
        setError(null)
        return true
      }
      const value = await apiClient.inventory(parkId)
      if (generation === loadGeneration.current) { setData(value); setGlobalCatalog(false); setTotal(value.part_count); setNextOffset(value.part_count); setError(null) }
      return true
    } catch (reason) {
      if (generation === loadGeneration.current) { setLoadFailed(true); setError(reason) }
      return false
    } finally {
      if (generation === loadGeneration.current) setLoadingMore(false)
    }
  }, [apiClient, parkId, query])
  useEffect(() => { setData(null); setTotal(0); setNextOffset(0); void load(); return () => { loadGeneration.current += 1 } }, [load])
  const currentData = data?.park_id === parkId ? data : null
  const component = currentData?.components.find(item => item.id === componentId)
  const part = useMemo(() => component?.parts.find(item => item.id === partId), [component, partId])
  useEffect(() => {
    if (!currentData) return
    const refreshedComponent = currentData.components.find(item => item.id === componentId)
    if (!refreshedComponent) {
      setComponentId(0); setPartId(0); setQuantity('1')
      return
    }
    const refreshedPart = refreshedComponent.parts.find(item => item.id === partId)
    if (!refreshedPart) {
      setPartId(0); setQuantity('1')
      return
    }
    if (isPositiveInventoryQuantity(quantity) && inventoryInt64Compare(quantity, refreshedPart.quantity) > 0) setQuantity('1')
  }, [componentId, currentData, partId, quantity])
  useEffect(() => {
    if (!activeQueuedAction) return
    idempotencyKey.current = activeQueuedAction.idempotencyKey
    const payload = activeQueuedAction.payload as { part_id?: unknown, quantity?: unknown }
    const queuedPartId = Math.abs(Number(payload.part_id))
    if (!currentData || !Number.isSafeInteger(queuedPartId)) return
    const queuedComponent = currentData.components.find(item => item.parts.some(candidate => candidate.id === queuedPartId))
    if (!queuedComponent) return
    setComponentId(queuedComponent.id)
    setPartId(queuedPartId)
    if (typeof payload.quantity === 'string') setQuantity(payload.quantity)
  }, [activeQueuedAction, currentData])
  useEffect(() => {
    if (pending || queueFailed) setReceipt('')
  }, [pending, queueFailed])
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (parkId == null || submitting.current || hydratingAction || pending || !part || !isPositiveInventoryQuantity(quantity) || inventoryInt64Compare(quantity, part.quantity) > 0) return
    submitting.current = true
    setBusy(true); setError(null); setLoadFailed(false); setReceipt('')
    try {
      idempotencyKey.current ??= globalThis.crypto.randomUUID()
      const adapterPartId = globalCatalog ? -part.id : part.id
      if (enqueueAction) {
        if (queueFailed && activeQueuedAction) {
          await cancelQueuedAction?.(activeQueuedAction.id)
          idempotencyKey.current = globalThis.crypto.randomUUID()
        }
        const input = buildInventoryWriteoffAction({
          issueKey,
          parkId,
          id: idempotencyKey.current,
          partId: adapterPartId,
          quantity,
        })
        const result = await enqueueAction(input)
        const action: OfflineAction = result && typeof result === 'object' && 'state' in result
          ? result as OfflineAction
          : { ...input, state: 'ready', attempts: 0, createdAt: Date.now(), updatedAt: Date.now() }
        setLocalQueuedAction(action)
        onQueued?.(action)
      } else {
        await apiClient.writeoffInventoryForTask(issueKey, adapterPartId, quantity, idempotencyKey.current)
      }
      if (!mounted.current) return
      if (enqueueAction) {
        setReceipt('')
        return
      }
      idempotencyKey.current = null
      setReceipt(`Списано: ${part.name} · ${quantity} шт. Место: ${part.location}`)
      setPartId(0); setQuantity('1')
      onWritten?.('Запчасть списана')
      await load(0, false)
    }
    catch (reason) { if (mounted.current) setError(reason) }
    finally { submitting.current = false; if (mounted.current) setBusy(false) }
  }
  if (parkId == null) return <div className="task-parts"><ErrorState description="Откройте задачу из доступного вам парка." title="Парк задачи недоступен" /><Button disabled type="button">Списать в задачу</Button></div>
  if (!currentData && !error) return <LoadingState label="Загружаем запчасти" />
  const failure = error ? classifyApiError(error, loadFailed
    ? receipt ? 'Не удалось обновить остатки. Списание повторять не нужно.' : 'Не удалось загрузить запчасти. Повторите загрузку.'
    : 'Не удалось списать запчасть.') : null
  return <div className="task-parts"><p>Выберите компоненту и запчасть. Остаток на складе уменьшится сразу. В чате появится сообщение для оператора. Оператор оформит расход в большой системе учёта.</p>
    {receipt ? <p role="status">{receipt}</p> : null}
    {failure ? <ErrorState description={failure.description} title={failure.title} /> : null}
    {actionHydrationError ? <ErrorState description="Повторите проверку очереди перед новым списанием." onRetry={onRetryActionHydration} title="Не удалось проверить ожидающее списание" /> : null}
    {queueFailed ? <ErrorState description="Проверьте остаток и повторите с теми же данными." title="Не удалось синхронизировать списание" /> : null}
    {failure ? <Button disabled={busy} onClick={() => void load()} type="button" variant="secondary">Обновить остатки</Button> : null}
    <fieldset disabled={busy || hydratingAction || pending} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
    {globalCatalog ? <form className="issue-action-row" onSubmit={event => { event.preventDefault(); setQuery(searchDraft.trim()) }}>
      <label className="field"><span>Название или артикул</span><input type="search" value={searchDraft} onChange={event => setSearchDraft(event.target.value)} /></label>
      <Button type="submit" variant="secondary">Найти</Button>
    </form> : null}
    {currentData ? <form aria-label="Списание запчасти" className="form-grid" onSubmit={submit}><label className="field"><span>Компонента</span><select required value={componentId || ''} onChange={event => { idempotencyKey.current = null; setComponentId(Number(event.target.value)); setPartId(0) }}><option value="">Выберите</option>{currentData.components.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {component?.has_photo ? <img alt={component.name} className="task-parts__component-photo" src={apiClient.inventoryComponentPhotoUrl(component.id)} /> : null}
      {component ? <label className="field"><span>Запчасть</span><select required value={partId || ''} onChange={event => { idempotencyKey.current = null; setPartId(Number(event.target.value)) }}><option value="">Выберите</option>{component.parts.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article} · {item.quantity} шт.</option>)}</select></label> : null}
      {part ? <article className="task-part-preview">{part.has_photo ? <img alt={part.name} src={apiClient.inventoryPartPhotoUrl(globalCatalog ? -part.id : part.id)} /> : null}<div><h3>{part.name}</h3><p>Артикул: {part.article}</p><p>Место: <strong>{part.location}</strong></p><StatusBadge tone={part.quantity !== '0' ? 'success' : 'critical'}>{part.quantity !== '0' ? `На складе: ${part.quantity}` : 'Нет на складе'}</StatusBadge></div></article> : null}
      {part ? <label className="field"><span>Списать, шт.</span><input inputMode="numeric" onChange={event => { idempotencyKey.current = null; setQuantity(event.target.value) }} pattern="[0-9]*" value={quantity} /></label> : null}{globalCatalog && nextOffset < total ? <Button busy={loadingMore} onClick={() => void load(nextOffset, true)} type="button" variant="secondary">Загрузить ещё</Button> : null}<Button busy={busy} disabled={hydratingAction || pending || !part || part.quantity === '0' || !isPositiveInventoryQuantity(quantity) || inventoryInt64Compare(quantity, part.quantity) > 0} type="submit">Списать в задачу</Button></form> : null}
    </fieldset>
  </div>
}
